"""会话隔离测试:画像按会话分文件 + 写入留痕(P1-1 修复)。

TDD 流程:当前实现画像写全局单文件(memory/user_profile.md),
本文件先在旧实现上运行,会话隔离相关用例必须失败(红);
替换为按会话分文件后全部通过(绿)。
"""
import unittest
import os
import sys
import tempfile
import shutil
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.logger import get_audit_logger
from auditronclaw.core.tools import builtins as builtins_module


class TestProfileSessionIsolation(unittest.TestCase):
    """画像必须按会话隔离:两个 thread 写入互不污染"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="auditronclaw_isolation_")
        # patch 后 MEMORY_DIR=self.tmp,_profile_path = <tmp>/profiles/<thread>.md
        self.profiles_dir = os.path.join(self.tmp, "profiles")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_profile_isolated_by_session(self):
        """不同会话的画像写入不同文件,互不污染(memory 目录为装配入参)"""
        # 会话 work 写入画像
        work_tool = builtins_module.create_profile_tool("work", self.tmp)
        result = work_tool.invoke({"new_content": "# work 画像\n- 工作:写报告"})
        self.assertIn("记忆档案已成功覆写更新", result)

        # 会话 personal 写入不同画像
        personal_tool = builtins_module.create_profile_tool("personal", self.tmp)
        result = personal_tool.invoke({"new_content": "# personal 画像\n- 爱好:跑步"})
        self.assertIn("记忆档案已成功覆写更新", result)

        # 两个会话的画像必须落在各自文件
        work_file = os.path.join(self.profiles_dir, "work.md")
        personal_file = os.path.join(self.profiles_dir, "personal.md")
        self.assertTrue(os.path.exists(work_file), "work 会话画像应写入 profiles/work.md")
        self.assertTrue(os.path.exists(personal_file), "personal 会话画像应写入 profiles/personal.md")
        with open(work_file, encoding="utf-8") as f:
            self.assertIn("写报告", f.read())
        with open(personal_file, encoding="utf-8") as f:
            content = f.read()
            self.assertNotIn("写报告", content)
            self.assertIn("跑步", content)

    def test_profile_diff_logged(self):
        """画像写入必须留痕:审计日志含行级增删 diff"""
        with patch.object(get_audit_logger(), "log_event") as mock_log:
            tool = builtins_module.create_profile_tool("work", self.tmp)
            tool.invoke({"new_content": "# 画像\n- 姓名:张三\n- 职业:工程师"})
            # 第二次写入,应产生 diff 留痕
            tool.invoke({"new_content": "# 画像\n- 姓名:张三\n- 职业:产品经理"})

            events = [c[1] for c in mock_log.call_args_list]
            diff_events = [e for e in events if e["event"] == "system_action"]
            self.assertTrue(diff_events, "画像写入应产生 system_action 审计事件")
            # 留痕应包含 diff 内容(增删行标记)
            last_event = diff_events[-1]["content"]
            self.assertIn("职业", last_event)

    def test_default_profile_migrated_from_legacy(self):
        """旧 user_profile.md 应迁移到 profiles/local_geek_master.md"""
        # 旧文件在 memory 目录根(user_profile.md),经装配入参指向 self.tmp
        legacy_file = os.path.join(self.tmp, "user_profile.md")
        os.makedirs(os.path.dirname(legacy_file), exist_ok=True)
        with open(legacy_file, "w", encoding="utf-8") as f:
            f.write("# 旧画像\n- 旧数据")

        builtins_module.migrate_legacy_profile("local_geek_master", self.tmp)

        migrated = os.path.join(self.profiles_dir, "local_geek_master.md")
        self.assertTrue(os.path.exists(migrated), "旧画像应迁移到 profiles/local_geek_master.md")
        self.assertFalse(os.path.exists(legacy_file), "迁移后旧文件应删除")


if __name__ == "__main__":
    unittest.main(verbosity=2)
