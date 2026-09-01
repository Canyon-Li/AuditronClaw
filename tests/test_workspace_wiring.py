"""工作区装配链集成测试(05 票票面验收项)。

一份临时 WorkspaceConfig 从入口形态出发,驱动全部落点消费者:
规则写入 cfg.approval_rules_file、任务落 cfg.tasks_file、办公工具写
cfg.office_dir、技能加载器扫 cfg.skills_dir、审计事件经 get_audit_logger
流出——全链没有一处读模块级路径常量(守卫见
tests/test_config_and_skill_loader.py)。

落点断言只看磁盘:消费者拿到的就是 cfg 派生的那份路径,写哪儿落哪儿。
审计部分截获在单点(auditronclaw.core.logger._audit_logger),验证
工具→审计的接线仍通。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.config import WorkspaceConfig


class WorkspaceWiringTestBase(unittest.TestCase):
    """公共底座:一份临时工作区,模拟入口装配(cfg.ensure_dirs)。"""

    def setUp(self):
        root = tempfile.mkdtemp(prefix="wiring_ws_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.cfg = WorkspaceConfig.from_root(root)
        self.cfg.ensure_dirs()


class TestWorkspaceDrivesAllConsumers(WorkspaceWiringTestBase):
    """cfg 一份到底:规则/任务/工位/技能/审计全部落在 cfg 派生路径。"""

    def test_rules_persist_lands_in_cfg(self):
        """审批规则写入 cfg.approval_rules_file,且在 office 之外(agent 写面够不着)"""
        from auditronclaw.core.approval.rules import RuleStore
        store = RuleStore(path=self.cfg.approval_rules_file)
        store.persist_rule("execute", "office/scripts/**", "approval",
                           thread_id="wiring_test")
        with open(self.cfg.approval_rules_file, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["scope"], "office/scripts/**")
        self.assertFalse(os.path.dirname(self.cfg.approval_rules_file)
                         .startswith(self.cfg.office_dir),
                         "规则文件必须在 office 之外")

    def test_task_tools_land_in_cfg_tasks_file(self):
        """任务工具落 cfg.tasks_file:schedule 与 desk 提交两路同文件"""
        from auditronclaw.core.tools.builtins import (
            build_builtin_tools, create_task_tools)
        tomorrow = "2099-01-01 09:00:00"
        tools = {t.name: t for t in create_task_tools(self.cfg.tasks_file)}
        receipt = tools["schedule_task"].invoke(
            {"target_time": tomorrow, "description": "wiring 集成任务"})
        self.assertIn("成功", receipt)
        # desk 提交工具走 build_builtin_tools 的同一条 tasks_file
        builtins = {t.name for t in build_builtin_tools(self.cfg, "wiring")}
        self.assertIn("submit_mailbox_desk_report", builtins)
        with open(self.cfg.tasks_file, encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertEqual([t["description"] for t in tasks], ["wiring 集成任务"])

    def test_office_tools_land_in_cfg_office_dir(self):
        """办公工具写 cfg.office_dir:相对路径解析锚定在工位内"""
        from auditronclaw.core.tools.sandbox_tools import build_office_tools
        tools = {t.name: t for t in build_office_tools(self.cfg.office_dir)}
        receipt = tools["write_office_file"].invoke(
            {"filepath": "wiring.md", "content": "落点集成"})
        self.assertIn("成功", receipt)
        with open(os.path.join(self.cfg.office_dir, "wiring.md"),
                  encoding="utf-8") as f:
            self.assertEqual(f.read(), "落点集成")

    def test_skill_loader_scans_cfg_skills_dir(self):
        """技能加载器扫 cfg.skills_dir:空卡槽返回空表,不碰别处"""
        from auditronclaw.core.skill_loader import load_dynamic_skills
        self.assertEqual(load_dynamic_skills(self.cfg.skills_dir,
                                             self.cfg.office_dir), [])

    def test_tools_emit_audit_events_through_logger(self):
        """工具运行经 get_audit_logger 出审计事件:装配链上审计接线仍通。

        按装配点同款接线(工厂工具过门),无人形态的高危写入被拒——
        审批对事件(approval_requested/decision)即落审计。"""
        from auditronclaw.core.approval.gate import wrap_all_tools
        from auditronclaw.core.tools.sandbox_tools import build_office_tools
        tools = {t.name: t for t in build_office_tools(self.cfg.office_dir)}
        gated = wrap_all_tools([tools["write_office_file"]], thread_id="wiring")
        with patch('auditronclaw.core.logger._audit_logger') as audit_mock:
            result = gated[0].invoke({"filepath": "audit_probe.md", "content": "x"})
        events = [c.kwargs.get("event")
                  for c in audit_mock.log_event.call_args_list]
        self.assertIn("approval_requested", events)
        self.assertIn("approval_decision", events)
        self.assertNotIn("成功", result, "无人形态的写必须被拒,不落盘")
        self.assertFalse(os.path.exists(
            os.path.join(self.cfg.office_dir, "audit_probe.md")))


if __name__ == '__main__':
    unittest.main()
