import unittest
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestConfig(unittest.TestCase):

    def test_config_import(self):
        """测试配置模块导入"""
        from auditronclaw.core.config import WORKSPACE_DIR, MEMORY_DIR, PERSONAS_DIR, SCRIPTS_DIR, OFFICE_DIR, SKILLS_DIR, DB_PATH, TASKS_FILE

        # 验证配置项存在
        self.assertIsInstance(WORKSPACE_DIR, str)
        self.assertIsInstance(MEMORY_DIR, str)
        self.assertIsInstance(PERSONAS_DIR, str)
        self.assertIsInstance(SCRIPTS_DIR, str)
        self.assertIsInstance(OFFICE_DIR, str)
        self.assertIsInstance(SKILLS_DIR, str)
        self.assertIsInstance(DB_PATH, str)
        self.assertIsInstance(TASKS_FILE, str)


class TestSkillLoader(unittest.TestCase):

    def test_skill_loader_import(self):
        """测试技能加载器模块导入"""
        try:
            from auditronclaw.core.skill_loader import load_dynamic_skills
            # 确保函数存在
            self.assertTrue(callable(load_dynamic_skills))
        except ImportError as e:
            # 如果导入失败，可能是因为依赖问题，但仍需确认模块结构
            self.fail(f"无法导入技能加载器: {e}")

    @patch('os.path.exists', return_value=False)
    @patch('os.listdir', side_effect=FileNotFoundError())
    def test_load_dynamic_skills_no_directory(self, mock_listdir, mock_exists):
        """测试技能加载器 - 不存在的目录"""
        from auditronclaw.core.skill_loader import load_dynamic_skills

        skills = load_dynamic_skills()
        self.assertEqual(skills, [])

    @patch('os.path.exists', return_value=True)
    @patch('os.listdir', return_value=[])
    def test_load_dynamic_skills_empty_directory(self, mock_listdir, mock_exists):
        """测试技能加载器 - 空目录"""
        from auditronclaw.core.skill_loader import load_dynamic_skills

        skills = load_dynamic_skills()
        self.assertEqual(skills, [])


if __name__ == '__main__':
    unittest.main()