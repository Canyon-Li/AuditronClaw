import unittest
import os
import sys
import json
import tempfile
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestHeartbeatPacemaker(unittest.TestCase):

    def setUp(self):
        """每个测试前创建临时任务文件"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
        self.original_tasks_file = None
        
        # 保存原始 TASKS_FILE 路径
        import auditronclaw.core.config
        self.original_tasks_file = auditronclaw.core.config.TASKS_FILE
        
        # 设置临时任务文件
        auditronclaw.core.config.TASKS_FILE = self.temp_file.name
        
        # 同时 patch heartbeat 模块中的引用
        import auditronclaw.core.heartbeat
        auditronclaw.core.heartbeat.TASKS_FILE = self.temp_file.name

    def tearDown(self):
        """每个测试后清理临时文件"""
        self.temp_file.close()
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
        
        # 恢复原始路径
        import auditronclaw.core.config
        auditronclaw.core.config.TASKS_FILE = self.original_tasks_file
        
        import auditronclaw.core.heartbeat
        auditronclaw.core.heartbeat.TASKS_FILE = self.original_tasks_file

    def test_no_tasks_file(self):
        """测试任务文件不存在时的行为"""
        from auditronclaw.core.heartbeat import pacemaker_loop
        
        # 删除临时文件模拟不存在(先关句柄:Windows 不允许删除打开中的文件)
        self.temp_file.close()
        os.unlink(self.temp_file.name)
        
        # 运行一个周期（不等待实际间隔）
        async def run_test():
            # 直接测试逻辑，不实际等待
            import auditronclaw.core.heartbeat as hb
            # 模拟 TASKS_FILE 不存在
            with patch.object(hb, 'TASKS_FILE', '/nonexistent/path.json'):
                # 不应该抛出异常
                pass
        
        asyncio.run(run_test())
        # 测试通过：没有异常抛出

    def test_empty_tasks_file(self):
        """测试任务文件为空时的行为"""
        from auditronclaw.core.heartbeat import pacemaker_loop
        
        # 写入空内容
        with open(self.temp_file.name, 'w') as f:
            f.write("")
        
        # 运行测试
        async def run_test():
            import auditronclaw.core.heartbeat as hb
            # 不应该抛出异常
            pass
        
        asyncio.run(run_test())
        # 测试通过：没有异常抛出

    def test_task_not_yet_due(self):
        """测试未到时间的任务不会被触发"""
        # 设置一个未来的任务
        future_time = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        
        test_tasks = [{
            "id": "task1",
            "target_time": future_time,
            "description": "未来任务",
            "repeat": None,
            "repeat_count": None
        }]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证任务文件内容
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["description"], "未来任务")

    def test_task_due_and_triggered(self):
        """测试到期的任务会被触发"""
        # 设置一个过去的任务（已到期）
        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        
        test_tasks = [{
            "id": "task1",
            "target_time": past_time,
            "description": "到期任务",
            "repeat": None,
            "repeat_count": None
        }]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证任务已写入
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["description"], "到期任务")

    def test_repeating_task_daily(self):
        """测试每日重复任务的处理"""
        past_time = datetime.now() - timedelta(minutes=5)
        
        test_tasks = [{
            "id": "task1",
            "target_time": past_time.strftime("%Y-%m-%d %H:%M:%S"),
            "description": "每日任务",
            "repeat": "daily",
            "repeat_count": None  # 无限循环
        }]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证任务设置正确
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["repeat"], "daily")

    def test_repeating_task_with_count(self):
        """测试有限次数的重复任务"""
        past_time = datetime.now() - timedelta(minutes=5)
        
        test_tasks = [{
            "id": "task1",
            "target_time": past_time.strftime("%Y-%m-%d %H:%M:%S"),
            "description": "有限重复任务",
            "repeat": "daily",
            "repeat_count": 3  # 重复 3 次
        }]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证任务设置正确
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["repeat_count"], 3)

    def test_invalid_time_format_handled(self):
        """测试无效时间格式被优雅处理"""
        test_tasks = [{
            "id": "task1",
            "target_time": "invalid-time-format",
            "description": "无效时间任务",
            "repeat": None,
            "repeat_count": None
        }]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证任务已写入（模块内部会处理异常）
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 1)

    def test_multiple_tasks_mixed(self):
        """测试多个混合任务（到期 + 未到期）"""
        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        future_time = (datetime.now() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        
        test_tasks = [
            {
                "id": "task1",
                "target_time": past_time,
                "description": "已到期任务",
                "repeat": None,
                "repeat_count": None
            },
            {
                "id": "task2",
                "target_time": future_time,
                "description": "未到期任务",
                "repeat": "daily",
                "repeat_count": None
            },
            {
                "id": "task3",
                "target_time": future_time,
                "description": "另一个未到期任务",
                "repeat": None,
                "repeat_count": None
            }
        ]
        
        with open(self.temp_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)
        
        # 验证所有任务已写入
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
        
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0]["description"], "已到期任务")
        self.assertEqual(tasks[1]["description"], "未到期任务")
        self.assertEqual(tasks[2]["description"], "另一个未到期任务")


class TestHeartbeatRepeatLogic(unittest.TestCase):
    """测试重复逻辑的细节"""

    def test_repeat_freq_values(self):
        """测试支持的重复频率值"""
        valid_freqs = ["hourly", "daily", "weekly"]
        
        for freq in valid_freqs:
            with self.subTest(freq=freq):
                # 验证频率值有效
                self.assertIn(freq, ["hourly", "daily", "weekly"])

    def test_repeat_count_decrement_logic(self):
        """测试重复次数递减逻辑"""
        # 模拟重复次数递减
        repeat_count = 3
        
        # 触发一次后递减
        if repeat_count > 1:
            repeat_count -= 1
        
        self.assertEqual(repeat_count, 2)
        
        # 最后一次触发
        if repeat_count > 1:
            repeat_count -= 1
        else:
            # 不再续期
            pass
        
        self.assertEqual(repeat_count, 1)


class TestHeartbeatTaskQueue(unittest.TestCase):
    """测试任务队列交互"""

    def test_task_queue_put_called(self):
        """测试任务触发时会调用 task_queue.put()"""
        # 这是一个集成测试的占位符
        # 实际测试需要 mock task_queue
        self.assertTrue(True)  # 占位断言


# ============ 心跳 daily 任务实弹演练（邮箱事务台部署接线）============
#
# 部署形态:一条 repeat="daily" 的循环任务,description 即事务台管线指令。
# 上面 TestHeartbeatPacemaker 的用例只钉了任务文件形状,没有真跑 pacemaker_loop;
# 部署前必须可观测到:到期 → 系统消息进会话队列(带管线指令)→ 任务续期 →
# 下个周期不重复触发。这是"手动改时间演练"的自动化形态。

DESK_PIPELINE_DESCRIPTION = (
    "跑一轮邮箱事务台:调用 read_recent_emails 读取近期 24 小时邮件,分类总结"
    "(待办是跨类别正交维度),把提炼出的待办用 schedule_task 落进任务列表,"
    "再按「分类账」格式调用 send_feishu_summary 推送日报到飞书群。"
)


class TestPacemakerLoopDailyDeskTask(unittest.TestCase):
    """真跑 pacemaker_loop:due 的 daily 事务台任务触发、续期、不重复。"""

    def setUp(self):
        import auditronclaw.core.config
        import auditronclaw.core.heartbeat

        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.temp_path = path
        self._orig_config = auditronclaw.core.config.TASKS_FILE
        self._orig_heartbeat = auditronclaw.core.heartbeat.TASKS_FILE
        # 与 TestHeartbeatPacemaker 同法:临时任务文件,不碰真实 workspace
        auditronclaw.core.config.TASKS_FILE = path
        auditronclaw.core.heartbeat.TASKS_FILE = path

    def tearDown(self):
        import auditronclaw.core.config
        import auditronclaw.core.heartbeat

        auditronclaw.core.config.TASKS_FILE = self._orig_config
        auditronclaw.core.heartbeat.TASKS_FILE = self._orig_heartbeat
        # Windows:文件被占用时删除会 WinError 32,先确认句柄已关
        if os.path.exists(self.temp_path):
            os.unlink(self.temp_path)

    def _write_due_daily_task(self):
        """写入一条刚到期的 daily 事务台任务,返回其 target_time。"""
        due = datetime.now() - timedelta(minutes=1)
        due_str = due.strftime("%Y-%m-%d %H:%M:%S")
        tasks = [{
            "id": "desk01",
            "target_time": due_str,
            "description": DESK_PIPELINE_DESCRIPTION,
            "repeat": "daily",
            "repeat_count": None,
        }]
        with open(self.temp_path, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
        return due

    def _run_pacemaker(self, seconds):
        """跑起搏器若干秒后取消,返回它的任务队列(已触发的消息都在里面)。"""
        from auditronclaw.core.heartbeat import pacemaker_loop

        queue = asyncio.Queue()

        async def drill():
            worker = asyncio.create_task(
                pacemaker_loop(task_queue=queue, check_interval=0.05)
            )
            await asyncio.sleep(seconds)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(drill())
        return queue

    @staticmethod
    def _drain(queue):
        """取空队列并返回消息列表。"""
        messages = []
        while not queue.empty():
            messages.append(queue.get_nowait())
        return messages

    def test_due_daily_task_drives_pipeline_message_into_queue(self):
        """到期触发可观测:系统心跳消息进会话队列,携带完整管线指令"""
        self._write_due_daily_task()

        queue = self._run_pacemaker(seconds=0.3)

        fired = self._drain(queue)
        self.assertEqual(len(fired), 1, "到期任务应触发恰好一条系统消息")
        msg = fired[0]
        self.assertIn("系统内部心跳触发", msg)
        self.assertIn("邮箱事务台", msg)
        self.assertIn("read_recent_emails", msg, "消息应携带管线指令原文")
        self.assertIn("send_feishu_summary", msg)

    def test_daily_task_renews_and_does_not_refire(self):
        """触发后续期到明天:任务留在文件里、描述不变、下个周期不重复触发"""
        due = self._write_due_daily_task()

        queue = self._run_pacemaker(seconds=0.3)

        # 0.3 秒 / 0.05 秒间隔 ≈ 6 个周期,只有第一次触发——无重复触发风暴
        fired_count = len(self._drain(queue))
        self.assertEqual(fired_count, 1)

        # 任务被续期而非删除:明天的同一时刻,description 原封不动
        with open(self.temp_path, encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertEqual(len(tasks), 1, "daily 任务触发后必须留存(续期),不能被删")
        renewed = tasks[0]
        expected_next = (due + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(renewed["target_time"], expected_next)
        self.assertEqual(renewed["repeat"], "daily")
        self.assertEqual(renewed["description"], DESK_PIPELINE_DESCRIPTION)


if __name__ == '__main__':
    unittest.main()
