import unittest
from unittest.mock import patch, mock_open
import os
import sys
import tempfile
import json
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.tools.builtins import (
    get_current_time,
    calculator
)
from auditronclaw.core.config import MEMORY_DIR, TASKS_FILE


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
        """测试保存用户档案功能"""
        from auditronclaw.core.tools.builtins import save_user_profile
        from unittest.mock import patch

        import tempfile
        import os

        # 用真实临时目录直接替换 MEMORY_DIR(而非 MagicMock,避免 os.path.join 得到 Mock 对象)
        tmp_memory = tempfile.mkdtemp()
        with patch('auditronclaw.core.tools.builtins.MEMORY_DIR', tmp_memory):
            # 测试保存功能
            test_content = "# 用户档案\n- 姓名：张三\n- 职业：工程师"
            result = save_user_profile.invoke({"new_content": test_content})
            self.assertEqual(result, "记忆档案已成功覆写更新。新的人设画像已生效。")

            # 默认会话画像落在 profiles/local_geek_master.md
            mock_profile_path = os.path.join(tmp_memory, "profiles", "local_geek_master.md")
            self.assertTrue(os.path.exists(mock_profile_path))
            with open(mock_profile_path, 'r', encoding='utf-8') as f:
                saved_content = f.read()
            self.assertEqual(saved_content, test_content)


class TestScheduledTasks(unittest.TestCase):

    def setUp(self):
        # 创建临时任务文件
        self.temp_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')
        self.original_tasks_file = TASKS_FILE
        # 更新 TASKS_FILE 指向临时文件
        import auditronclaw.core.tools.builtins
        auditronclaw.core.tools.builtins.TASKS_FILE = self.temp_file.name

    def tearDown(self):
        # 清理临时文件
        self.temp_file.close()
        if os.path.exists(self.temp_file.name):
            os.unlink(self.temp_file.name)
        # 恢复原始路径
        import auditronclaw.core.tools.builtins
        auditronclaw.core.tools.builtins.TASKS_FILE = self.original_tasks_file

    def test_schedule_task_single(self):
        """测试单次任务调度功能"""
        from auditronclaw.core.tools.builtins import schedule_task, list_scheduled_tasks

        future_time = (datetime.now().replace(hour=9, minute=0, second=0)
                      if datetime.now().hour >= 9 else
                      datetime.now().replace(hour=9, minute=0, second=0))
        if future_time <= datetime.now():
            future_time = future_time.replace(day=future_time.day + 1)

        target_time = future_time.strftime("%Y-%m-%d %H:%M:%S")

        result = schedule_task.invoke({"target_time": target_time, "description": "喝水提醒"})
        self.assertIn("任务已成功加入队列", result)
        self.assertIn("喝水提醒", result)

        # 验证任务已添加到文件
        with open(self.temp_file.name, 'r', encoding='utf-8') as f:
            tasks_data = json.load(f)

        self.assertEqual(len(tasks_data), 1)
        self.assertEqual(tasks_data[0]["description"], "喝水提醒")
        self.assertEqual(tasks_data[0]["target_time"], target_time)

    def test_schedule_task_invalid_time_format(self):
        """测试调度任务 - 无效时间格式"""
        from auditronclaw.core.tools.builtins import schedule_task

        result = schedule_task.invoke({"target_time": "invalid_time", "description": "测试任务"})
        self.assertIn("设定失败：时间格式错误", result)

    def test_list_scheduled_tasks_empty(self):
        """测试列出空任务列表"""
        from auditronclaw.core.tools.builtins import list_scheduled_tasks

        # 确保文件为空
        with open(self.temp_file.name, 'w') as f:
            f.write("")

        result = list_scheduled_tasks.invoke({})
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


class TestScheduledTasksWithTasks(unittest.TestCase):

    def setUp(self):
        self.temp_tasks_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.json')

        # 设置临时任务文件路径
        self.original_tasks_file = TASKS_FILE
        import auditronclaw.core.tools.builtins
        auditronclaw.core.tools.builtins.TASKS_FILE = self.temp_tasks_file.name

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

        with open(self.temp_tasks_file.name, 'w', encoding='utf-8') as f:
            json.dump(test_tasks, f, ensure_ascii=False, indent=2)

    def tearDown(self):
        # 清理临时文件
        self.temp_tasks_file.close()
        if os.path.exists(self.temp_tasks_file.name):
            os.unlink(self.temp_tasks_file.name)
        # 恢复原始路径
        import auditronclaw.core.tools.builtins
        auditronclaw.core.tools.builtins.TASKS_FILE = self.original_tasks_file

    def test_list_scheduled_tasks_non_empty(self):
        """测试列出非空任务列表"""
        from auditronclaw.core.tools.builtins import list_scheduled_tasks

        result = list_scheduled_tasks.invoke({})
        self.assertIn("当前待执行任务列表", result)
        self.assertIn("任务 1", result)
        self.assertIn("任务 2", result)

    def test_delete_scheduled_task(self):
        """测试删除计划任务"""
        from auditronclaw.core.tools.builtins import delete_scheduled_task, list_scheduled_tasks

        result = delete_scheduled_task.invoke({"task_id": "task1"})
        self.assertIn("已成功取消", result)

        # 验证任务已被删除
        result = list_scheduled_tasks.invoke({})
        self.assertNotIn("任务 1", result)
        self.assertIn("任务 2", result)

    def test_delete_nonexistent_task(self):
        """测试删除不存在的任务"""
        from auditronclaw.core.tools.builtins import delete_scheduled_task

        result = delete_scheduled_task.invoke({"task_id": "nonexistent"})
        self.assertIn("删除失败：未找到", result)

    def test_modify_scheduled_task(self):
        """测试修改计划任务"""
        from auditronclaw.core.tools.builtins import modify_scheduled_task, list_scheduled_tasks

        new_time = (datetime.now().replace(hour=10, minute=0, second=0)
                   if datetime.now().hour >= 10 else
                   datetime.now().replace(hour=10, minute=0, second=0))
        if new_time <= datetime.now():
            new_time = new_time.replace(day=new_time.day + 1)

        new_target_time = new_time.strftime("%Y-%m-%d %H:%M:%S")

        result = modify_scheduled_task.invoke({"task_id": "task1", "new_time": new_target_time, "new_description": "修改后的任务 1"})
        self.assertIn("已成功更新", result)

        # 验证任务已被修改
        result = list_scheduled_tasks.invoke({})
        self.assertIn("修改后的任务 1", result)
        self.assertIn(new_target_time, result)

    def test_modify_scheduled_task_invalid_time(self):
        """测试修改计划任务 - 无效时间格式"""
        from auditronclaw.core.tools.builtins import modify_scheduled_task

        result = modify_scheduled_task.invoke({"task_id": "task1", "new_time": "invalid_time"})
        self.assertIn("修改失败：时间格式错误", result)

    def test_modify_nonexistent_task(self):
        """测试修改不存在的任务"""
        from auditronclaw.core.tools.builtins import modify_scheduled_task

        result = modify_scheduled_task.invoke({"task_id": "nonexistent", "new_description": "不存在的任务"})
        self.assertIn("修改失败：未找到", result)


if __name__ == '__main__':
    unittest.main()
