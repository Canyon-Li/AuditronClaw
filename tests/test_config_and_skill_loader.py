import unittest
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestConfig(unittest.TestCase):

    def test_config_module_has_no_path_constants(self):
        """终态守卫(05 票):config 模块只剩装配期对象,import 期路径常量不得回潮。

        常量会把路径绑定进每个导入者(旧 reload 链时代的病根);
        路径一律经 WorkspaceConfig 构造、装配期注入。"""
        import auditronclaw.core.config as config_module
        for legacy in ("WORKSPACE_DIR", "MEMORY_DIR", "PERSONAS_DIR", "SCRIPTS_DIR",
                       "OFFICE_DIR", "SKILLS_DIR", "DB_PATH", "TASKS_FILE",
                       "APPROVAL_RULES_FILE", "LOG_DIR", "APPROVAL_TIMEOUT_SECONDS"):
            with self.subTest(name=legacy):
                self.assertFalse(hasattr(config_module, legacy),
                                 f"config.{legacy} 是 import 期路径常量,不得回潮")


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

    def test_load_dynamic_skills_no_directory(self):
        """测试技能加载器 - 不存在的目录"""
        from auditronclaw.core.skill_loader import load_dynamic_skills

        tmp = tempfile.mkdtemp(prefix="skills_missing_")
        missing = os.path.join(tmp, "no_such_skills")
        skills = load_dynamic_skills(missing, tmp)
        self.assertEqual(skills, [])

    def test_load_dynamic_skills_empty_directory(self):
        """测试技能加载器 - 空目录"""
        from auditronclaw.core.skill_loader import load_dynamic_skills

        tmp = tempfile.mkdtemp(prefix="skills_empty_")
        skills_dir = os.path.join(tmp, "office", "skills")
        os.makedirs(skills_dir)
        skills = load_dynamic_skills(skills_dir, os.path.dirname(skills_dir))
        self.assertEqual(skills, [])


if __name__ == '__main__':
    unittest.main()