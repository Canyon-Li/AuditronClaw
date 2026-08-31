"""静态名册装配(票 02):合并册构造 + 四条 meta-test + 注入接缝。

票 02 的验收口径:忘登记、同名冲突、低报自报全部在装配期或测试期爆掉,
运行期不存在静默不可用。本模块是引信——
- 装配期(roster.build_static_risk):跨来源同名、自报值出词汇、动词钉子;
- 测试期(meta-test 1-4):装配事实(core 工厂产物 ∪ 各域 register().tools;
  skills / extras 明文排除——设计上 unclassified fail-closed)与分级名册
  逐名对照,缺口即红;
- 注入点:classifier builtin 判定带册、门转发册、装配点构造册注入,
  shell / 绑定域 / 技能 / 外接路径不涉册不受影响。

测试夹具域在此先行自报 risk(ADR-002 槽位表:生产首个静态分级域留待
下一个新域,不造 stub 域凑验证)。
"""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from helpers import production_builtin_tools

from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval import roster
from auditronclaw.core.approval.classifier import (
    RISK_DELETE,
    RISK_READ,
    RISK_UNCLASSIFIED,
    RISK_WRITE,
    _BOUND_DOMAIN_TOOLS,
    _SHELL_TOOLS,
    _core_static_risk,
    classify_tool_call,
)
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.domain import DomainRegistration


def _fixture_domain(risk: dict) -> DomainRegistration:
    """测试夹具域:自报静态分级;tools 为空(空 tools 仅夹具域许可)。"""
    return DomainRegistration(tools=(), risk=risk)


def _peek_tool():
    """夹具域工具:自报 read 的只读形状。"""
    @tool
    def peek_inbox(hours: int) -> str:
        """夹具:只读收件箱摘要"""
        return "empty"
    return peek_inbox


def _production_registrations() -> list:
    """装配点喂给合并册的全部生产域 register() 产物——直接读 agent 导出的
    单一来源(表追加域 + 特例原位路径的 feishu),测试侧不自行复述接线。"""
    from auditronclaw.core.agent import production_roster_registrations
    return production_roster_registrations()


def _factory_tool_names() -> set:
    """core 工厂产物名(临时工作区上装配一次;先例:基线夹具采集)。

    经 helpers.production_builtin_tools 走生产同款装配——"工厂产物"含经
    参数注入的 feishu 域工具,meta-test 1 的覆盖面随之覆盖到它(对绑定域册
    的归属检查由此真实发生)。
    """
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="roster_meta_ws_"))
    try:
        workspace.ensure_dirs()
        return {t.name for t in production_builtin_tools(workspace, "meta_probe")}
    finally:
        shutil.rmtree(workspace.root, ignore_errors=True)


# ============ 合并册构造:装配期就该爆的三类 + 形态 ============

class TestMergedRosterConstruction(unittest.TestCase):
    """合并册 = core 静态册(12 名存量原地)∪ 各域自报;frozen 名册。"""

    def test_no_domains_equals_core_static_roster(self):
        """零域装配:合并册逐名逐级等于 core 静态册,存量 12 名原地不动"""
        merged = roster.build_static_risk()
        self.assertEqual(dict(merged), _core_static_risk())
        self.assertEqual(len(merged), 12,
                         "core 静态册存量口径:纯读 6 / 写 5 / 删 1(改动此数须过评审)")

    def test_domain_self_report_merges_in(self):
        """域自报逐名入册;core 存量不受域装配影响"""
        merged = roster.build_static_risk(
            _fixture_domain({"sync_calendar": RISK_WRITE, "peek_inbox": RISK_READ}))
        self.assertEqual(merged["sync_calendar"], RISK_WRITE)
        self.assertEqual(merged["peek_inbox"], RISK_READ)
        self.assertEqual(merged["calculator"], RISK_READ)

    def test_merged_roster_is_read_only(self):
        """装配后不许再改:写册必 TypeError(与 DomainRegistration 的 frozen 同一风格)"""
        merged = roster.build_static_risk(_fixture_domain({"peek_inbox": RISK_READ}))
        with self.assertRaises(TypeError):
            merged["evil_tool"] = RISK_READ

    def test_out_of_vocabulary_category_rejected(self):
        """自报值必须落静态分级词汇:条件分级产物/垃圾值装配期拒"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(_fixture_domain({"run_backup": "execute"}))
        self.assertIn("run_backup", str(ctx.exception))
        self.assertIn("静态分级词汇", str(ctx.exception))


# ============ meta-test 1:装配出的静态工具名 ∈ 分级名册 ============

class TestMeta1AssembledStaticToolsRostered(unittest.TestCase):
    """meta-test 1:装配出的每个静态工具名 ∈ 合并册 ∪ core 绑定域册。

    静态集合机器可判:core 工厂产物 ∪ 各域 register().tools;skills 与
    extras 明文排除(设计上 unclassified fail-closed,不参加名册)。
    shell 工具是第三条按名路径(命令段级判定),与两册并列为覆盖面——
    忘登记在它那里同样爆红,不因判定按段而漏网。
    """

    def test_every_assembled_name_is_rostered(self):
        registrations = _production_registrations()
        merged = roster.build_static_risk(*registrations)
        assembled = _factory_tool_names() \
            | {t.name for reg in registrations for t in reg.tools}
        rostered = set(merged) | set(_BOUND_DOMAIN_TOOLS) | set(_SHELL_TOOLS)
        unrostered = assembled - rostered
        self.assertFalse(
            unrostered,
            f"装配出的工具名不在任何分级名册(忘登记):{sorted(unrostered)}")


# ============ meta-test 2:两册互斥 ============

class TestMeta2RosterMutualExclusion(unittest.TestCase):
    """meta-test 2:合并册 ∩ core 绑定域册 = ∅;core 静态册不含域声明名。"""

    def test_merged_roster_disjoint_from_bound_domain_roster(self):
        """域自报不得遮蔽条件分级:合并册里不得出现绑定域工具名"""
        merged = roster.build_static_risk(*_production_registrations())
        overlap = set(merged) & set(_BOUND_DOMAIN_TOOLS)
        self.assertFalse(overlap,
                         f"合并册与 core 绑定域册同名:{sorted(overlap)}——"
                         f"条件分级判定会被静态自报遮蔽")

    def test_core_static_roster_has_no_domain_declared_names(self):
        """core 静态册不得含任何已被域声明的名字(跨来源同名的存量侧防线)"""
        declared: set = set()
        for reg in _production_registrations():
            declared |= set(reg.risk)
        overlap = set(_core_static_risk()) & declared
        self.assertFalse(overlap,
                         f"core 静态册与域自报同名:{sorted(overlap)}")


# ============ meta-test 3:动词钉子 ============

class TestMeta3VerbNail(unittest.TestCase):
    """meta-test 3:首词落动词集而自报 READ 必红;豁免清单外不得绕过。

    治理条目(动词集/豁免清单只增不删、变更进 PR 评审)写在钉子旁:
    auditronclaw/core/approval/roster.py。
    """

    def test_assembled_roster_has_no_low_report(self):
        """当刻装配出的名册零低报:首词落动词集且自报 read 的名字不存在"""
        merged = roster.build_static_risk(*_production_registrations())
        violations = {
            name: level for name, level in merged.items()
            if level == RISK_READ
            and name not in roster.VERB_NAIL_EXEMPTIONS
            and name.split("_", 1)[0].lower() in roster.RISKY_NAME_VERBS}
        self.assertEqual(violations, {},
                         f"动词打头却自报纯读(疑似低报):{violations}")

    def test_nail_bites_fixture_low_report(self):
        """钉子要咬人:夹具域自报"动词打头却 read"装配期 RuntimeError"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(_fixture_domain({"delete_old_reports": RISK_READ}))
        self.assertIn("delete_old_reports", str(ctx.exception))
        self.assertIn("delete", str(ctx.exception))

    def test_first_word_match_not_substring(self):
        """首词整词匹配非子串:get_writer_info 含 write 子串但首词是 get,不误伤"""
        merged = roster.build_static_risk(_fixture_domain({"get_writer_info": RISK_READ}))
        self.assertEqual(merged["get_writer_info"], RISK_READ)

    def test_exempt_list_is_the_only_bypass(self):
        """豁免清单是唯一绕过通道:清单点名后同款名字放行"""
        with patch.object(roster, "VERB_NAIL_EXEMPTIONS",
                          frozenset({"download_logs"})):
            merged = roster.build_static_risk(
                _fixture_domain({"download_logs": RISK_READ}))
            self.assertEqual(merged["download_logs"], RISK_READ)

    def test_verb_set_nonempty_and_exemptions_exercised(self):
        """钉子不得被静默拔掉:动词集非空;豁免清单里每个名都在册且自报 read"""
        self.assertTrue(roster.RISKY_NAME_VERBS,
                        "动词集为空 = 钉子已拔,低报防线不存在")
        merged = roster.build_static_risk(*_production_registrations())
        for name in roster.VERB_NAIL_EXEMPTIONS:
            self.assertEqual(merged.get(name), RISK_READ,
                             f"豁免名 {name} 不在册或非 read——豁免清单失效条目")


# ============ meta-test 4:跨来源同名 ============

class TestMeta4CrossSourceNameConflict(unittest.TestCase):
    """meta-test 4:跨来源同名(不论级别)装配期构造必 RuntimeError;级别只进报错信息。"""

    def test_domain_vs_core_same_name_same_level_raises(self):
        """不论级别:与 core 同名且同级(write vs write)同样爆"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(_fixture_domain({"write_office_file": RISK_WRITE}))
        self.assertIn("write_office_file", str(ctx.exception))
        self.assertIn("core 静态册", str(ctx.exception))

    def test_domain_vs_core_same_name_levels_only_in_message(self):
        """级别只进报错信息:两边级别都在话里供诊断,不影响是否报错"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(
                _fixture_domain({"delete_scheduled_task": RISK_READ}))
        message = str(ctx.exception)
        self.assertIn("delete_scheduled_task", message)
        self.assertIn("read", message)   # 域自报级别
        self.assertIn("delete", message)  # core 侧级别(与工具名同词,双保险取首词断言)

    def test_domain_vs_domain_same_name_raises(self):
        """域间同名同爆:来源不只 core,任何两域撞名都在装配期拒"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(_fixture_domain({"sync_notes": RISK_WRITE}),
                                     _fixture_domain({"sync_notes": RISK_READ}))
        self.assertIn("sync_notes", str(ctx.exception))

    def test_domain_vs_bound_domain_roster_raises(self):
        """绑定域册同受保护:域自报条件分级工具名 = 遮蔽运行时判定,装配期拒"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(
                _fixture_domain({"send_feishu_summary": RISK_READ}))
        self.assertIn("send_feishu_summary", str(ctx.exception))
        self.assertIn("core 绑定域册", str(ctx.exception))

    def test_domain_vs_shell_tool_rejected(self):
        """shell 工具名同受保护:自报静态级别 = 遮蔽命令段判定,装配期拒"""
        with self.assertRaises(RuntimeError) as ctx:
            roster.build_static_risk(
                _fixture_domain({"execute_office_shell": RISK_READ}))
        self.assertIn("execute_office_shell", str(ctx.exception))
        self.assertIn("shell 工具", str(ctx.exception))


# ============ 注入点:classifier 带册判定 ============

class TestClassifierCarriesRoster(unittest.TestCase):
    """builtin 判定路径带册:传入的册决定静态分级;其余来源与 shell 不涉册。"""

    def test_roster_entry_drives_classification(self):
        """带册判定:域自报 write 必批;同一名字缺省(无册)unclassified 必批"""
        merged = roster.build_static_risk(_fixture_domain({"sync_calendar": RISK_WRITE}))
        self.assertEqual(
            classify_tool_call("sync_calendar", {}, static_risk=merged).risk_class,
            RISK_WRITE)
        self.assertEqual(classify_tool_call("sync_calendar", {}).risk_class,
                         RISK_UNCLASSIFIED)

    def test_domain_write_has_no_fabricated_target(self):
        """域自报写类提不出可信目标作用域:targets 空(规则无从豁免,fail-closed)"""
        merged = roster.build_static_risk(_fixture_domain({"sync_calendar": RISK_WRITE}))
        assess = classify_tool_call("sync_calendar", {"note": "x"}, static_risk=merged)
        self.assertEqual(assess.risk_class, RISK_WRITE)
        self.assertEqual(assess.targets, ())

    def test_shell_judgment_cannot_be_shadowed_by_roster(self):
        """命令段判定册遮蔽不了:册里塞 shell 工具名也不改段级结果(判定期兜底)"""
        rogue = {"execute_office_shell": RISK_READ}
        assess = classify_tool_call("execute_office_shell",
                                    {"command": "rm x"}, static_risk=rogue)
        self.assertEqual(assess.risk_class, RISK_DELETE, assess.reason)

    def test_shell_bound_skill_extra_paths_ignore_roster(self):
        """shell 段级 / 绑定域 / 技能 / 外接判定不涉册:传不传册同果"""
        merged = roster.build_static_risk(_fixture_domain({"peek_inbox": RISK_READ}))
        for kwargs in ({}, {"static_risk": merged}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(
                    classify_tool_call("execute_office_shell",
                                       {"command": "mkdir x"}, **kwargs).risk_class,
                    RISK_WRITE)
                self.assertEqual(
                    classify_tool_call("send_feishu_summary",
                                       {"summary_text": "x"}, **kwargs).risk_class,
                    RISK_READ)
                self.assertEqual(
                    classify_tool_call("web_spider", {"mode": "help"},
                                       provenance="skill",
                                       skill_folder="web_spider", **kwargs).risk_class,
                    RISK_READ)
                self.assertEqual(
                    classify_tool_call("any", {}, provenance="extra", **kwargs).risk_class,
                    RISK_UNCLASSIFIED)


# ============ 注入点:门转发册 ============

class TestGateRosterInjection(unittest.TestCase):
    """门带册:wrap_tool 注入的名册就是 builtin 判定用的册。"""

    def setUp(self):
        _patcher = patch('auditronclaw.core.logger._audit_logger')
        self.audit_mock = _patcher.start()
        self.addCleanup(_patcher.stop)

    def test_injected_roster_drives_gate(self):
        """自报 read 经注入册放行直通;同一工具无册时 unclassified 无人拒"""
        from auditronclaw.core.approval.gate import REJECT_PHRASE, wrap_tool
        merged = roster.build_static_risk(_fixture_domain({"peek_inbox": RISK_READ}))
        gated = wrap_tool(_peek_tool(), thread_id="roster_gate_test",
                          static_risk=merged)
        self.assertEqual(gated.invoke({"hours": 1}), "empty")

        no_roster = wrap_tool(_peek_tool(), thread_id="roster_gate_test")
        rejected = no_roster.invoke({"hours": 1})
        self.assertIn(REJECT_PHRASE, rejected, "缺省册无此名必须按 unclassified 拒")


# ============ 注入点:装配点构造并注入 ============

class TestAssemblyWiring(unittest.TestCase):
    """装配点接线:register() 的域工具进装配表,自报进合并册注入门。"""

    def test_domain_tools_and_roster_flow_through_assembly(self):
        from auditronclaw.core.agent import create_agent_app

        def fixture_register() -> DomainRegistration:
            return DomainRegistration(tools=(_peek_tool(),),
                                      risk={"peek_inbox": RISK_READ})

        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="roster_asm_ws_"))
        workspace.ensure_dirs()
        self.addCleanup(shutil.rmtree, workspace.root, True)
        with ExitStack() as stack:
            stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                                      return_value=llm_mock))
            stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                                      return_value=[]))
            stack.enter_context(patch('auditronclaw.core.agent._DOMAIN_REGISTRARS',
                                      (fixture_register,)))
            stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
            create_agent_app(provider_name="fake", model_name="fake-model",
                             workspace=workspace, checkpointer=MemorySaver(),
                             thread_id="roster_asm_test")
            bound = llm_mock.bind_tools.call_args[0][0]
            names = [t.name for t in bound]
            expected = [t.name for t in production_builtin_tools(workspace,
                                                                 "roster_asm_test")]
            # 域工具紧随内置工厂产物、先于技能装配(默认装配技能为空)
            self.assertEqual(names, expected + ["peek_inbox"])
            peek = next(t for t in bound if t.name == "peek_inbox")
            self.assertTrue(peek.metadata.get("approval_gate"), "域工具必须过门")
            # 自报 read 经门直通——合并册注入生效的端到端证据
            self.assertEqual(peek.invoke({"hours": 1}), "empty")

    def test_production_domains_empty_until_first_domain(self):
        """现状钉子:registrars 接线表为零。

        feishu(03 票已迁入域包)不走本表——其工具经 build_builtin_tools
        参数插回迁移前原位,保装配顺序与改造前基线一致;两类来源(原位
        参数注入 vs 表追加)的收口统一是票 04 的对象,届时本断言翻红
        提醒接线确认(不是阻力,是检查点)。
        """
        from auditronclaw.core.agent import _DOMAIN_REGISTRARS
        self.assertEqual(_DOMAIN_REGISTRARS, ())


if __name__ == '__main__':
    unittest.main()
