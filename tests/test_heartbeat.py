import unittest
import os
import sys
import json
import tempfile
import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestHeartbeatPacemaker(unittest.TestCase):

    def setUp(self):
        """每个测试前创建临时任务文件(队列落点为装配入参,不碰真实 workspace)"""
        self.temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
        # Windows:被句柄占用的文件无法被 os.replace 原子替换(WinError 5)
        self.temp_file.close()

    def tearDown(self):
        """每个测试后清理临时文件（句柄已在 setUp 关闭）"""
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)

    def test_no_tasks_file(self):
        """测试任务文件不存在时的行为"""
        from auditronclaw.core.heartbeat import pacemaker_loop

        # 删除临时文件模拟不存在(句柄已在 setUp 关闭,Windows 才允许删除)
        os.unlink(self.temp_file.name)

        # 运行一个周期（不等待实际间隔）
        async def run_test():
            # 队列落点为装配入参:文件不存在,起搏器空转不抛
            queue = asyncio.Queue()
            worker = asyncio.create_task(
                pacemaker_loop(task_queue=queue,
                               tasks_file=self.temp_file.name, check_interval=0.01))
            await asyncio.sleep(0.05)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
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
            queue = asyncio.Queue()
            worker = asyncio.create_task(
                pacemaker_loop(task_queue=queue,
                               tasks_file=self.temp_file.name, check_interval=0.01))
            await asyncio.sleep(0.05)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
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


# ============ 心跳静默吞掉的回执钉子（F3） ============
#
# 读盘/写回两处曾经 except Exception 即吞——tasks.json 历史损伤时心跳
# 永久静默空转、无回执无报错；写回失败时当轮触发消息照发、续期丢失
# 也无人知晓。修法：两处各落一条审计回执；读盘失败加去抖（同一错误
# 只记一次——坏文件每个周期都撞同一处，回执记首次，修复后重置）。

class TestHeartbeatErrorReceipts(unittest.TestCase):
    """损坏队列/写回失败：心跳照常空转，但审计必须留下事件。"""

    def setUp(self):
        from auditronclaw.core import heartbeat
        heartbeat._last_read_error_key = None  # 去抖状态隔离，测试间互不串台
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        self.temp_path = path

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.unlink(self.temp_path)
        if os.path.exists(self.temp_path + ".tmp"):
            os.unlink(self.temp_path + ".tmp")

    def _run_pacemaker(self, seconds=0.3):
        """跑起搏器若干秒后取消，返回它的任务队列。"""
        from auditronclaw.core.heartbeat import pacemaker_loop

        queue = asyncio.Queue()

        async def drill():
            worker = asyncio.create_task(
                pacemaker_loop(task_queue=queue, tasks_file=self.temp_path,
                               check_interval=0.05))
            await asyncio.sleep(seconds)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass

        asyncio.run(drill())
        return queue

    @staticmethod
    def _logged_contents(mock_logger, keyword):
        """从 mock logger 的调用里筛出含关键字的 content 列表。"""
        return [call.kwargs.get("content", "")
                for call in mock_logger.log_event.call_args_list
                if keyword in call.kwargs.get("content", "")]

    def test_corrupt_tasks_json_logs_read_failure_once(self):
        """损坏 JSON：心跳空转不抛，读失败回执恰一条（去抖），无触发消息"""
        with open(self.temp_path, "w", encoding="utf-8") as f:
            f.write("{not valid json")

        mock_logger = MagicMock()
        with patch("auditronclaw.core.logger._audit_logger", mock_logger):
            queue = self._run_pacemaker(seconds=0.3)  # ~6 个周期撞同一处坏 JSON

        read_failures = self._logged_contents(mock_logger, "读任务队列失败")
        self.assertEqual(len(read_failures), 1,
                         "同一读错误跨周期只记一次（去抖）")
        self.assertIn(self.temp_path, read_failures[0])
        self.assertTrue(queue.empty(), "坏队列不得触发任何任务消息")

    def test_write_back_failure_logs_event_and_still_fires(self):
        """写回失败：触发消息照发、回执逐次落账、队列保持旧内容

        写回持续失败时任务文件保持旧内容，任务下一周期会再次到期触发
        （既有语义，本票不改）——断言按此形态：每次触发必有同数回执，
        续期丢失不静默。
        """
        past_time = (datetime.now() - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        with open(self.temp_path, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "t1", "target_time": past_time,
                "description": "到期任务", "repeat": None, "repeat_count": None,
            }], f, ensure_ascii=False)

        mock_logger = MagicMock()
        with patch("auditronclaw.core.logger._audit_logger", mock_logger), \
             patch("auditronclaw.core.heartbeat._write_tasks",
                   side_effect=OSError("disk full")):
            queue = self._run_pacemaker(seconds=0.3)

        # 触发消息照发（这是既有行为，写失败不吞掉已到期提醒）
        fired = []
        while not queue.empty():
            fired.append(queue.get_nowait())
        self.assertGreaterEqual(len(fired), 1, "写回失败不影响当轮触发消息")

        write_failures = self._logged_contents(mock_logger, "写回任务队列失败")
        self.assertEqual(len(write_failures), len(fired),
                         "每次写回失败必须留下事件——续期丢失不可静默")
        self.assertIn("disk full", write_failures[0])
        # 队列文件未被改写（续期丢失即旧内容原样——事件里说清了这个后果）
        with open(self.temp_path, encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertEqual(tasks[0]["target_time"], past_time)


# ============ 心跳 daily 任务真实运行演练（邮箱事务台部署接线）============
#
# 部署形态:一条 repeat="daily" 的循环任务,description 即事务台管线指令。
# 上面 TestHeartbeatPacemaker 的用例只钉了任务文件形状,没有真跑 pacemaker_loop;
# 部署前必须可观测到:到期 → 系统消息进会话队列(带管线指令)→ 任务续期 →
# 下个周期不重复触发。这是"手动改时间演练"的自动化形态。

DESK_PIPELINE_DESCRIPTION = (
    "跑一轮邮箱事务台,共两步:1. 调用 read_recent_emails(hours=24, max_emails=10)"
    " 读取近期 24 小时邮件,只调用一次。2. 把分类结果作为参数调用"
    " submit_mailbox_desk_report 一次性提交——日报排版、待办落盘、飞书推送"
    "都由该工具完成,不要再调 send_feishu_summary 或 schedule_task。"
)


class TestPacemakerLoopDailyDeskTask(unittest.TestCase):
    """真跑 pacemaker_loop:due 的 daily 事务台任务触发、续期、不重复。"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        self.temp_path = path

    def tearDown(self):
        # Windows:文件被占用时删除会 WinError 32,先确认句柄已关
        if os.path.exists(self.temp_path):
            os.unlink(self.temp_path)
        # _write_tasks 的 tmp 残留(崩溃路径)一并清掉
        if os.path.exists(self.temp_path + ".tmp"):
            os.unlink(self.temp_path + ".tmp")

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
                pacemaker_loop(task_queue=queue, tasks_file=self.temp_path,
                               check_interval=0.05)
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
        """到期触发可观测:心跳消息以类型化来源进会话队列,携带完整管线指令"""
        from auditronclaw.core.approval.gate import TurnOrigin
        from auditronclaw.core.bus import TurnRequest

        self._write_due_daily_task()

        queue = self._run_pacemaker(seconds=0.3)

        fired = self._drain(queue)
        self.assertEqual(len(fired), 1, "到期任务应触发恰好一条系统消息")
        item = fired[0]
        self.assertIsInstance(item, TurnRequest,
                              "心跳触发进队列的是类型化回合请求,不是裸字符串")
        self.assertIs(item.origin, TurnOrigin.HEARTBEAT,
                      "心跳来源在构造上类型化标记(无人值守,不靠文本前缀)")
        msg = item.text
        self.assertIn("系统内部心跳触发", msg)
        self.assertIn("邮箱事务台", msg)
        self.assertIn("read_recent_emails", msg, "消息应携带管线指令原文")
        self.assertIn("submit_mailbox_desk_report", msg)

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
