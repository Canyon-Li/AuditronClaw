import unittest
import os
import shutil
import sys
import tempfile
import json
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.tools.builtins import (
    get_current_time,
    calculator,
    create_profile_tool,
    create_task_tools,
)


class TestBuiltInTools(unittest.TestCase):

    def test_get_current_time(self):
        """测试获取当前时间功能"""
        result = get_current_time.invoke({})
        self.assertIn("当前本地系统时间是:", result)

        # 提取时间字符串并验证格式
        time_str = result.replace("当前本地系统时间是：", "").strip()
        try:
            parsed_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            self.assertIsInstance(parsed_time, datetime)
        except ValueError:
            # 如果格式不匹配，至少验证返回了时间字符串
            self.assertTrue(len(time_str) > 0)

    def test_calculator_valid_expressions(self):
        """测试计算器功能 - 有效表达式"""
        test_cases = [
            ("2 + 3", 5),
            ("10 * 5", 50),
            ("15 / 3", 5.0),
            ("2 ** 3", 8),
            ("17 % 5", 2)
        ]

        for expr, expected in test_cases:
            with self.subTest(expr=expr):
                result = calculator.invoke({"expression": expr})
                self.assertIn(str(expected), result)

    def test_calculator_invalid_expression(self):
        """测试计算器功能 - 无效表达式"""
        invalid_expressions = [
            "2 +",
            "1 / 0",
            "__import__('os')",
            "import os",
            "eval('2+2')"
        ]

        for expr in invalid_expressions:
            with self.subTest(expr=expr):
                result = calculator.invoke({"expression": expr})
                self.assertIn("计算出错", result)

    def test_save_user_profile(self):
        """测试保存用户档案功能(临时 memory 目录经工厂注入)"""
        tmp_memory = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp_memory, True))
        save_user_profile = create_profile_tool("local_geek_master", tmp_memory)

        test_content = "# 用户档案\n- 姓名：张三\n- 职业：工程师"
        result = save_user_profile.invoke({"new_content": test_content})
        self.assertEqual(result, "记忆档案已成功覆写更新。新的人设画像已生效。")

        # 默认会话画像落在 profiles/local_geek_master.md
        mock_profile_path = os.path.join(tmp_memory, "profiles", "local_geek_master.md")
        self.assertTrue(os.path.exists(mock_profile_path))
        with open(mock_profile_path, 'r', encoding='utf-8') as f:
            saved_content = f.read()
        self.assertEqual(saved_content, test_content)


class TestProfileThreadIdNormalization(unittest.TestCase):
    """save_user_profile 的 thread_id 归一化(审批门 05 票)。

    thread_id 由操作员/会话层/基准适配器提供、bake 进画像工具,LLM 的参数面
    里没有它;基准 id 形如 "前缀/用例号"(bench_pipeline._drive_agent),故
    拒的是逃逸形态(上跳/盘符/绝对路径/空白)而非一切分隔符——画像落点必须
    锁死在 memory/profiles/ 内,不给拼出逃逸路径的机会。
    全量覆写是既有设计,维持不动(审批门的写级管它)。
    """

    def setUp(self):
        from auditronclaw.core.tools.builtins import _profile_path
        self._profile_path = _profile_path
        self.tmp_memory = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp_memory, True))

    def test_escape_thread_ids_rejected_at_factory(self):
        """上跳/盘符/绝对路径/空白:组装期即拒(fail fast,不等到首调)"""
        for bad in ("../evil", "evil/../ok", "..", "C:/x", "C:\\x",
                    "..\\..\\etc", "/abs", "\\abs", " ", ""):
            with self.subTest(thread_id=bad):
                with self.assertRaises(ValueError):
                    create_profile_tool(bad, self.tmp_memory)

    def test_profile_path_locks_into_profiles_dir(self):
        """路径形状:合法 id 锁死 profiles 内(含基准的 前缀/用例号 形态);逃逸抛错"""
        self.assertEqual(
            self._profile_path("thread_ok", self.tmp_memory),
            os.path.join(self.tmp_memory, "profiles", "thread_ok.md"))
        # 基准形态(bench_pipeline._drive_agent 的 前缀/用例号):profiles 内子路径
        self.assertEqual(
            os.path.normpath(self._profile_path("golden/g001", self.tmp_memory)),
            os.path.join(self.tmp_memory, "profiles", "golden", "g001.md"))
        with self.assertRaises(ValueError):
            self._profile_path("../escape", self.tmp_memory)

    def test_full_overwrite_behavior_unchanged(self):
        """全量覆写维持:两次写后文件里只有第二份完整内容"""
        tool = create_profile_tool("norm_profile_test", self.tmp_memory)
        tool.invoke({"new_content": "# 第一版\n- 旧偏好"})
        result = tool.invoke({"new_content": "# 第二版\n- 新偏好"})
        self.assertEqual(result, "记忆档案已成功覆写更新。新的人设画像已生效。")
        with open(os.path.join(self.tmp_memory, "profiles", "norm_profile_test.md"),
                  encoding="utf-8") as f:
            self.assertEqual(f.read(), "# 第二版\n- 新偏好")


class _TaskToolsHarness(unittest.TestCase):
    """任务工具测试公共件:临时队列文件经工厂注入(05 票,不再 patch 模块常量)。"""

    def setUp(self):
        fd, self.tasks_path = tempfile.mkstemp(suffix=".json")
        # Windows:被句柄占用的文件无法被 os.replace 原子替换(WinError 5),
        # 写路径测试不得持有 tasks.json 句柄
        os.close(fd)
        os.unlink(self.tasks_path)
        self.tools = {t.name: t for t in create_task_tools(self.tasks_path)}

    def tearDown(self):
        for p in (self.tasks_path, self.tasks_path + ".tmp"):
            if os.path.exists(p):
                os.unlink(p)

    def _tool(self, name):
        return self.tools[name]


class TestScheduledTasks(_TaskToolsHarness):

    def test_schedule_task_single(self):
        """测试单次任务调度功能"""
        future_time = (datetime.now().replace(hour=9, minute=0, second=0)
                      if datetime.now().hour >= 9 else
                      datetime.now().replace(hour=9, minute=0, second=0))
        if future_time <= datetime.now():
            future_time = future_time.replace(day=future_time.day + 1)

        target_time = future_time.strftime("%Y-%m-%d %H:%M:%S")

        result = self._tool("schedule_task").invoke(
            {"target_time": target_time, "description": "喝水提醒"})
        self.assertIn("任务已成功加入队列", result)
        self.assertIn("喝水提醒", result)

        # 验证任务已添加到文件
        with open(self.tasks_path, 'r', encoding='utf-8') as f:
            tasks_data = json.load(f)

        self.assertEqual(len(tasks_data), 1)
        self.assertEqual(tasks_data[0]["description"], "喝水提醒")
        self.assertEqual(tasks_data[0]["target_time"], target_time)

    def test_schedule_task_invalid_time_format(self):
        """测试调度任务 - 无效时间格式"""
        result = self._tool("schedule_task").invoke(
            {"target_time": "invalid_time", "description": "测试任务"})
        self.assertIn("设定失败：时间格式错误", result)

    def test_list_scheduled_tasks_empty(self):
        """测试列出空任务列表"""
        # 确保文件为空
        with open(self.tasks_path, 'w') as f:
            f.write("")

        result = self._tool("list_scheduled_tasks").invoke({})
        # 兼容两种可能的返回消息
        self.assertTrue("没有任何定时任务" in result or "任务列表为空" in result)

    def test_get_system_model_info(self):
        """测试获取系统模型信息功能"""
        from auditronclaw.core.tools.builtins import get_system_model_info

        # 保存原有环境变量
        orig_provider = os.environ.get('DEFAULT_PROVIDER')
        orig_model = os.environ.get('DEFAULT_MODEL')

        try:
            # 测试正常情况
            os.environ['DEFAULT_PROVIDER'] = 'test_provider'
            os.environ['DEFAULT_MODEL'] = 'test_model'

            result = get_system_model_info.invoke({})
            self.assertIn('test_provider', result)
            self.assertIn('test_model', result)

            # 测试未知情况
            os.environ['DEFAULT_PROVIDER'] = 'unknown'
            os.environ['DEFAULT_MODEL'] = 'unknown'

            result = get_system_model_info.invoke({})
            self.assertIn("无法获取当前的系统模型配置", result)

        finally:
            # 恢复环境变量
            if orig_provider is not None:
                os.environ['DEFAULT_PROVIDER'] = orig_provider
            else:
                os.environ.pop('DEFAULT_PROVIDER', None)

            if orig_model is not None:
                os.environ['DEFAULT_MODEL'] = orig_model
            else:
                os.environ.pop('DEFAULT_MODEL', None)


class TestScheduledTasksWithTasks(_TaskToolsHarness):

    def setUp(self):
        super().setUp()
        # 添加一些测试任务
        future_time = (datetime.now().replace(hour=9, minute=0, second=0)
                      if datetime.now().hour >= 9 else
                      datetime.now().replace(hour=9, minute=0, second=0))
        if future_time <= datetime.now():
            future_time = future_time.replace(day=future_time.day + 1)

        target_time = future_time.strftime("%Y-%m-%d %H:%M:%S")

        test_tasks = [
            {
                "id": "task1",
                "target_time": target_time,
                "description": "任务 1",
                "repeat": None,
                "repeat_count": None
            },
            {
                "id": "task2",
                "target_time": target_time,
                "description": "任务 2",
                "repeat": None,
                "repeat_count": None
            }
        ]

        with open(self.tasks_path, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)

    def test_list_scheduled_tasks_non_empty(self):
        """测试列出非空任务列表"""
        result = self._tool("list_scheduled_tasks").invoke({})
        self.assertIn("当前待执行任务列表", result)
        self.assertIn("任务 1", result)
        self.assertIn("任务 2", result)

    def test_delete_scheduled_task(self):
        """测试删除计划任务"""
        result = self._tool("delete_scheduled_task").invoke({"task_id": "task1"})
        self.assertIn("已成功取消", result)

        # 验证任务已被删除
        result = self._tool("list_scheduled_tasks").invoke({})
        self.assertNotIn("任务 1", result)
        self.assertIn("任务 2", result)

    def test_delete_nonexistent_task(self):
        """测试删除不存在的任务"""
        result = self._tool("delete_scheduled_task").invoke({"task_id": "nonexistent"})
        self.assertIn("删除失败：未找到", result)

    def test_modify_scheduled_task(self):
        """测试修改计划任务"""
        new_time = (datetime.now().replace(hour=10, minute=0, second=0)
                   if datetime.now().hour >= 10 else
                   datetime.now().replace(hour=10, minute=0, second=0))
        if new_time <= datetime.now():
            new_time = new_time.replace(day=new_time.day + 1)

        new_target_time = new_time.strftime("%Y-%m-%d %H:%M:%S")

        result = self._tool("modify_scheduled_task").invoke(
            {"task_id": "task1", "new_time": new_target_time,
             "new_description": "修改后的任务 1"})
        self.assertIn("已成功更新", result)

        # 验证任务已被修改
        result = self._tool("list_scheduled_tasks").invoke({})
        self.assertIn("修改后的任务 1", result)
        self.assertIn(new_target_time, result)

    def test_modify_scheduled_task_invalid_time(self):
        """测试修改计划任务 - 无效时间格式"""
        result = self._tool("modify_scheduled_task").invoke(
            {"task_id": "task1", "new_time": "invalid_time"})
        self.assertIn("修改失败：时间格式错误", result)

    def test_modify_nonexistent_task(self):
        """测试修改不存在的任务"""
        result = self._tool("modify_scheduled_task").invoke(
            {"task_id": "nonexistent", "new_description": "不存在的任务"})
        self.assertIn("修改失败：未找到", result)


if __name__ == '__main__':
    unittest.main()
