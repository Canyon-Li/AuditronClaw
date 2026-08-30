"""审批规则 02 票:高危的唯一豁免通道(规则系统)。

分层(沿用仓库测试纪律):
- 纯函数单测:scope_matches / rule_matches / 分级器目标提取(targets)
- 真实组件行为测试:RuleStore 落盘(冷启动/铸规则/撤销)、门接真实 matcher、
  装配点接线、规则文件对 agent 写面不可达

词汇见 CONTEXT.md「审批规则/副作用分级/审批门」。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from auditronclaw.core.approval.classifier import (
    RISK_DELETE,
    RISK_EXECUTE,
    RISK_WRITE,
    RiskAssessment,
    classify_shell_command,
    classify_tool_call,
)
from auditronclaw.core.tools.sandbox_tools import SYS_OS


def _assess(risk_class, targets=()):
    """测试用分级结果:级别 + 目标作用域(规则匹配只吃这两样)。"""
    return RiskAssessment(tool="t", risk_class=risk_class, reason="测试",
                          targets=tuple(targets))


# ============ 作用域模式匹配(纯函数:攻击与合法的分界线) ============

from auditronclaw.core.approval.rules import (
    APPROVAL_ACTIONS,
    ApprovalRule,
    RuleSource,
    RuleStore,
    make_rule_matcher,
    rule_matches,
    scope_matches,
)


class TestScopePatternMatching(unittest.TestCase):
    """路径感知 glob:段以 / 切分,** 跨段,* 只在段内。"""

    def test_exact_scope_matches_only_itself(self):
        """精确作用域:write→tasks.json 只匹配 tasks.json 本身"""
        self.assertTrue(scope_matches("tasks.json", "tasks.json"))
        self.assertFalse(scope_matches("tasks.json", "office/tasks.json"))
        self.assertFalse(scope_matches("tasks.json", "tasks.json.bak"))

    def test_double_star_covers_subtree(self):
        """** 覆盖子树(含多级与目录自身)"""
        self.assertTrue(scope_matches("office/scripts/**", "office/scripts/run.py"))
        self.assertTrue(scope_matches("office/scripts/**", "office/scripts/a/b.py"))
        self.assertTrue(scope_matches("office/scripts/**", "office/scripts"))

    def test_double_star_boundary_is_attack_line(self):
        """作用域边界:scripts/ 规则不匹配 office 根目录脚本与同前缀兄弟目录"""
        self.assertFalse(scope_matches("office/scripts/**", "office/evil.py"),
                         "office 根目录脚本不得被 scripts/ 规则放行——攻击与合法的分界线")
        self.assertFalse(scope_matches("office/scripts/**", "office/scripts_evil/x.py"),
                         "同前缀兄弟目录不是子树")

    def test_single_star_stays_in_segment(self):
        """* 只匹配一段:不得跨 / 越段"""
        self.assertTrue(scope_matches("office/*.py", "office/a.py"))
        self.assertFalse(scope_matches("office/*.py", "office/a/b.py"))

    def test_case_and_separator_normalized(self):
        """大小写不敏感、反斜杠等价正斜杠(Windows 文件系统语义)"""
        self.assertTrue(scope_matches("Office/Scripts/**", "office\\scripts\\run.py"))
        self.assertTrue(scope_matches("office/scripts/**", "OFFICE/SCRIPTS/RUN.PY"))

    def test_domain_scope_same_matcher(self):
        """域名作用域复用同一匹配(单段内 * 跨字符;05 票域名扩展的地基)"""
        self.assertTrue(scope_matches("open.feishu.cn", "open.feishu.cn"))
        self.assertTrue(scope_matches("*.feishu.cn", "open.feishu.cn"))
        self.assertFalse(scope_matches("*.feishu.cn", "evil.example"))


class TestRuleMatchFunction(unittest.TestCase):
    """规则匹配纯函数:动作相等 + 全部目标作用域被覆盖才命中。"""

    def _rule(self, action, scope):
        return ApprovalRule(id="r1", action=action, scope=scope,
                            source=RuleSource.APPROVAL.value,
                            created_at="2026-08-27T00:00:00Z")

    def test_action_and_scope_both_required(self):
        """命中=动作相等且作用域覆盖;缺一即不命中"""
        rule = self._rule(RISK_EXECUTE, "office/scripts/**")
        self.assertIsNotNone(rule)
        self.assertTrue(rule_matches(rule, _assess(RISK_EXECUTE, ["office/scripts/run.py"])))
        # 动作不符:execute 规则不放行 write
        self.assertFalse(rule_matches(rule, _assess(RISK_WRITE, ["office/scripts/run.py"])))
        # 作用域不覆盖:根目录脚本(分界线)
        self.assertFalse(rule_matches(rule, _assess(RISK_EXECUTE, ["office/evil.py"])))

    def test_all_targets_must_be_covered(self):
        """部分覆盖不命中:mv 触碰作用域外文件时不得被自动放行"""
        rule = self._rule(RISK_WRITE, "office/scripts/**")
        assessment = _assess(RISK_WRITE, ["office/root_cfg.py", "office/scripts/b.py"])
        self.assertFalse(rule_matches(rule, assessment),
                         "目标作用域有一处未被覆盖即不得放行")

    def test_no_targets_no_match(self):
        """提不出目标作用域的调用(未入册/外接)任何规则都不豁免,不猜"""
        rule = self._rule(RISK_WRITE, "**")
        self.assertFalse(rule_matches(rule, _assess(RISK_WRITE, [])))

    def test_read_class_never_matches(self):
        """免批级不进规则世界(可铸动作集就不含 read)"""
        self.assertNotIn("read", APPROVAL_ACTIONS)
        rule = self._rule(RISK_WRITE, "tasks.json")
        self.assertFalse(rule_matches(rule, _assess("read", ["tasks.json"])))


# ============ 分级器目标提取:规则匹配的输入(与执行同源) ============

class TestClassifierTargets(unittest.TestCase):
    """targets 是分级结果的组成部分:提取与执行落点用同一套路径基准。"""

    def test_write_office_file_target(self):
        """写文件:office 相对路径 → workspace 相对目标(office/ 前缀)"""
        assess = classify_tool_call("write_office_file",
                                    {"filepath": "scripts/a.py", "content": "x"})
        self.assertEqual(assess.targets, ("office/scripts/a.py",))

    def test_write_target_same_normalization_as_execution(self):
        """带冗余 office/ 前缀与反斜杠的路径,目标归一与执行落点一致。

        反斜杠剥前缀是 Windows 语义(_normalize_office_path 文档):非
        Windows 下 \ 是合法文件名字符,office\scripts\a.py 不剥前缀、
        如实落在 office 内层——期望随平台分案,CI(Linux)与本地
        (Windows)同一断言各自成立(2026-08-27 CI 独红发现)。
        """
        cases = {"office/scripts/a.py": "office/scripts/a.py",
                 "scripts\\a.py": "office/scripts/a.py",
                 "office\\scripts\\a.py": ("office/scripts/a.py"
                                           if SYS_OS == "Windows"
                                           else "office/office/scripts/a.py")}
        for filepath, expected in cases.items():
            with self.subTest(filepath=filepath):
                assess = classify_tool_call("write_office_file",
                                            {"filepath": filepath, "content": "x"})
                self.assertEqual(assess.targets, (expected,))

    def test_profile_tool_targets_profile_dir(self):
        """画像写:目标=memory/profiles(会话内具体文件由工具自析)"""
        assess = classify_tool_call("save_user_profile", {"new_content": "x"})
        self.assertEqual(assess.targets, ("memory/profiles",))

    def test_task_tools_target_tasks_json(self):
        """任务队列落盘部:目标=tasks.json(写与删同目标)"""
        self.assertEqual(
            classify_tool_call("schedule_task",
                               {"target_time": "2026-08-28 08:00", "description": "d"}).targets,
            ("tasks.json",))
        self.assertEqual(
            classify_tool_call("delete_scheduled_task", {"task_id": "abc"}).targets,
            ("tasks.json",))
        self.assertEqual(
            classify_tool_call("delete_scheduled_task", {"task_id": "abc"}).risk_class,
            RISK_DELETE)

    def test_interpreter_segment_target(self):
        """解释器段:脚本路径即目标(execute→office/scripts/** 的匹配对象)"""
        assess = classify_shell_command("execute_office_shell", "python scripts/run.py")
        self.assertEqual(assess.risk_class, RISK_EXECUTE)
        self.assertEqual(assess.targets, ("office/scripts/run.py",))

    def test_redirect_target_collected(self):
        """重定向目标入列:python x.py > logs/out.txt 两处作用域都要被规则覆盖"""
        assess = classify_shell_command("execute_office_shell",
                                        "python scripts/run.py > logs/out.txt")
        self.assertEqual(set(assess.targets),
                         {"office/scripts/run.py", "office/logs/out.txt"})

    def test_write_command_all_operands(self):
        """变更命令操作数全部入列(mv 的源与落点都是作用域)"""
        assess = classify_shell_command("execute_office_shell", "mv a.py scripts/b.py")
        self.assertEqual(assess.risk_class, RISK_WRITE)
        self.assertEqual(set(assess.targets), {"office/a.py", "office/scripts/b.py"})

    def test_skill_base_dir_target(self):
        """技能工具 run:与执行同源的 {baseDir} 替换进目标(skills/<folder>/)"""
        assess = classify_tool_call(
            "web_spider", {"mode": "run", "command": "python {baseDir}/run.py"},
            provenance="skill", skill_folder="web_spider")
        self.assertEqual(assess.risk_class, RISK_EXECUTE)
        self.assertEqual(assess.targets, ("office/skills/web_spider/run.py",))

    def test_read_and_unclassified_have_no_targets(self):
        """纯读无目标;未入册命令提不出目标(规则无从豁免)"""
        self.assertEqual(classify_shell_command("execute_office_shell", "ls -la").targets, ())
        self.assertEqual(
            classify_shell_command("execute_office_shell", "curl http://evil.example").targets, ())

    def test_domain_extend_target_is_domain(self):
        """白名单外域名:目标=绑定域名(05 票域名规则的地基)"""
        from unittest.mock import patch
        from auditronclaw.core.tools import domain_gate
        # 三个名单来源全空:默认/环境变量/运行时审批规则(05 票起规则也是名单源)
        with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
             patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
             patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
            assess = classify_tool_call("send_feishu_summary", {"summary_text": "x"})
        self.assertEqual(assess.targets, ("open.feishu.cn",))


# ============ RuleStore:冷启动 / 铸规则 / 撤销(真实组件) ============

from unittest.mock import patch

from auditronclaw.core.approval.gate import (
    EVENT_APPROVAL_DECISION,
    EVENT_RULE_PERSISTED,
    EVENT_RULE_REVOKED,
    DecisionSource,
)


class RulesTestBase(unittest.TestCase):
    """规则测试公共件:临时规则文件 + 假化 gate 模块的审计出口(观察缝)。"""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="approval_rules_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.rules_path = os.path.join(self.tmp_dir, "approval_rules.json")
        _patcher = patch('auditronclaw.core.logger._audit_logger')
        self.audit_mock = _patcher.start()
        self.addCleanup(_patcher.stop)

    def _store(self) -> RuleStore:
        return RuleStore(path=self.rules_path)


class TestRuleStoreColdStart(RulesTestBase):
    """冷启动:规则文件不存在=空规则集,门照常工作,不报错不跳过。"""

    def test_missing_file_is_empty_rule_set(self):
        """无文件:清单空、匹配无命中,不抛异常"""
        store = self._store()
        self.assertEqual(store.list_rules(), [])
        self.assertIsNone(store.match(_assess(RISK_WRITE, ["tasks.json"])))
        self.assertFalse(os.path.exists(self.rules_path), "读操作不得顺手建文件")

    def test_empty_file_is_empty_rule_set(self):
        """空 JSON 数组同样=空规则集"""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            f.write("[]")
        store = self._store()
        self.assertEqual(store.list_rules(), [])

    def test_corrupt_file_fails_closed(self):
        """损坏文件按空规则集处理(fail-closed:豁免归零,门照常拒)"""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            f.write("{oops 不是 json")
        store = self._store()
        self.assertEqual(store.list_rules(), [])
        self.assertIsNone(store.match(_assess(RISK_WRITE, ["tasks.json"])))

    def test_invalid_entries_skipped_valid_kept(self):
        """非法条目跳过(缺字段/动作不可铸/出处未知),合法条目保留"""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "ok1", "action": "write", "scope": "tasks.json",
                 "source": "approval", "created_at": "2026-08-27T00:00:00Z"},
                {"id": "bad1", "action": "read", "scope": "x",
                 "source": "approval", "created_at": "2026-08-27T00:00:00Z"},
                {"id": "bad2", "action": "write",
                 "source": "approval", "created_at": "2026-08-27T00:00:00Z"},
                {"id": "bad3", "action": "write", "scope": "x",
                 "source": "whoever", "created_at": "2026-08-27T00:00:00Z"},
                "not-a-dict",
            ], f, ensure_ascii=False)
        rules = self._store().list_rules()
        self.assertEqual([r.id for r in rules], ["ok1"])


class TestRuleStorePersist(RulesTestBase):
    """铸规则:条目落盘 + rule_persisted 入审计("永久允许"的内部接口)。"""

    def test_persist_creates_entry_and_file(self):
        """条目五字段齐全;文件是 JSON 数组,重开 store 可读回"""
        rule = self._store().persist_rule(
            RISK_EXECUTE, "office/scripts/**", RuleSource.APPROVAL.value,
            thread_id="persist_test")
        self.assertTrue(rule.id)
        self.assertEqual(rule.action, RISK_EXECUTE)
        self.assertEqual(rule.scope, "office/scripts/**")
        self.assertEqual(rule.source, "approval")
        self.assertTrue(rule.created_at.startswith("20"), "created_at 是 ISO 时间戳")
        # 落盘形状:JSON 数组、字段与条目一致
        with open(self.rules_path, encoding="utf-8") as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk, [rule.to_dict()])
        # 新 store(同路径)读回同一批规则
        self.assertEqual(self._store().list_rules(), [rule])

    def test_persist_emits_rule_persisted_audit(self):
        """铸规则即留痕:rule_persisted 事件携带条目整体"""
        rule = self._store().persist_rule(
            RISK_WRITE, "tasks.json", RuleSource.APPROVAL.value,
            thread_id="persist_test")
        (call,) = self.audit_mock.log_event.call_args_list
        self.assertEqual(call.kwargs, {
            "thread_id": "persist_test",
            "event": EVENT_RULE_PERSISTED,
            "rule": rule.to_dict(),
        })

    def test_persist_is_idempotent_per_action_scope(self):
        """同动作同作用域不重复铸:返回既有条目,文件单条、事件只一次"""
        store = self._store()
        first = store.persist_rule(RISK_WRITE, "tasks.json", "approval")
        second = store.persist_rule(RISK_WRITE, "tasks.json", "approval")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(store.list_rules()), 1)
        self.assertEqual(
            [c for c in self.audit_mock.log_event.call_args_list
             if c.kwargs.get("event") == EVENT_RULE_PERSISTED].__len__(), 1)

    def test_persist_validates_action_scope_source(self):
        """非法输入直接拒:不可铸动作、空/带空白作用域、未知出处"""
        store = self._store()
        with self.assertRaises(ValueError):
            store.persist_rule("read", "tasks.json", "approval")
        with self.assertRaises(ValueError):
            store.persist_rule(RISK_WRITE, "  ", "approval")
        with self.assertRaises(ValueError):
            store.persist_rule(RISK_WRITE, " tasks.json", "approval")
        with self.assertRaises(ValueError):
            store.persist_rule(RISK_WRITE, "tasks.json", "whoever")
        self.assertEqual(store.list_rules(), [], "非法铸规则不得落任何条目")
        self.assertFalse(os.path.exists(self.rules_path))


class TestRuleStoreRevoke(RulesTestBase):
    """撤销:即失效并留审计;撤销后同动作再触发审批。"""

    def _persisted(self):
        store = self._store()
        rule = store.persist_rule(RISK_WRITE, "tasks.json", "approval",
                                  thread_id="revoke_test")
        return store, rule

    def test_revoke_removes_entry_and_emits_audit(self):
        """撤销移除条目 + rule_revoked 事件携带被撤条目"""
        store, rule = self._persisted()
        removed = store.revoke_rule(rule.id, thread_id="revoke_test")
        self.assertEqual(removed.id, rule.id)
        self.assertEqual(store.list_rules(), [])
        revoked_calls = [c for c in self.audit_mock.log_event.call_args_list
                         if c.kwargs.get("event") == EVENT_RULE_REVOKED]
        (call,) = revoked_calls
        self.assertEqual(call.kwargs, {
            "thread_id": "revoke_test",
            "event": EVENT_RULE_REVOKED,
            "rule": rule.to_dict(),
        })

    def test_revoke_unknown_id_raises(self):
        """撤销不存在的规则:KeyError,文件不动"""
        store, rule = self._persisted()
        with open(self.rules_path, encoding="utf-8") as f:
            before = f.read()
        with self.assertRaises(KeyError):
            store.revoke_rule("no-such-id")
        with open(self.rules_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_revoked_rule_no_longer_matches(self):
        """撤销即失效:匹配读不到该条目(即时读盘,不重启)"""
        store, rule = self._persisted()
        assessment = _assess(RISK_WRITE, ["tasks.json"])
        self.assertIsNotNone(store.match(assessment))
        store.revoke_rule(rule.id)
        self.assertIsNone(store.match(assessment))


def _write_stub(calls: list):
    """名为 write_office_file 的测试桩:分级按工具名入册,桩身只记录。

    门测的是"分级→规则→放行/拒绝"链,不需要真写盘——放行路径不触真文件。
    """
    from langchain_core.tools import StructuredTool

    def run(filepath: str, content: str, mode: str = "w") -> str:
        calls.append((filepath, content))
        return f" ● 成功以 覆盖/新建 模式写入文件:{filepath}"
    return StructuredTool.from_function(
        func=run, name="write_office_file", description="测试桩:写工具")


class TestGateWithRealRules(RulesTestBase):
    """门接真实规则:命中放行(source=rule_auto 且带 rule_id),越界照拒。"""

    def _gated(self, store):
        from auditronclaw.core.approval.gate import wrap_tool
        return wrap_tool(_write_stub(self.calls), thread_id="gate_rules_test",
                         rule_matcher=make_rule_matcher(store))

    def setUp(self):
        super().setUp()
        self.calls = []

    def test_rule_hit_passes_with_rule_auto_and_rule_id(self):
        """命中:放行执行 + 决定事件 source=rule_auto 且携带 rule_id"""
        store = self._store()
        rule = store.persist_rule(RISK_WRITE, "office/**", "approval")
        gated = self._gated(store)
        result = gated.invoke({"filepath": "reports/daily.md", "content": "x"})
        self.assertEqual(self.calls, [("reports/daily.md", "x")],
                         "规则命中的写必须照常执行")
        self.assertIn("成功", result)
        decision = [c.kwargs for c in self.audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], True)
        self.assertEqual(decision["source"], DecisionSource.RULE_AUTO.value)
        self.assertEqual(decision["rule_id"], rule.id, "决定事件要能核出是哪条规则放的行")

    def test_scope_boundary_still_rejected_unattended(self):
        """越界目标(office 根目录脚本不在 scripts/ 规则内):照拒且 source=unattended"""
        from auditronclaw.core.approval.gate import REJECT_PHRASE
        from langchain_core.tools import StructuredTool

        def shell_run(command: str) -> str:
            self.calls.append(command)
            return "executed"
        shell_stub = StructuredTool.from_function(
            func=shell_run, name="execute_office_shell", description="测试桩:shell")

        store = self._store()
        store.persist_rule(RISK_EXECUTE, "office/scripts/**", "approval")
        from auditronclaw.core.approval.gate import wrap_tool
        gated = wrap_tool(shell_stub, thread_id="gate_rules_test",
                          rule_matcher=make_rule_matcher(store))
        result = gated.invoke({"command": "python evil.py"})
        self.assertIn(REJECT_PHRASE, result, "根目录脚本不得被 scripts/ 规则放行")
        self.assertEqual(self.calls, [], "被拒的命令不得执行")
        decision = [c.kwargs for c in self.audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.UNATTENDED.value)

    def test_revoke_then_same_call_triggers_approval_again(self):
        """撤销后同动作再触发审批:原本放行的调用回到无人拒(票面钉死项)"""
        from auditronclaw.core.approval.gate import REJECT_PHRASE
        store = self._store()
        rule = store.persist_rule(RISK_WRITE, "office/**", "approval")
        gated = self._gated(store)
        gated.invoke({"filepath": "a_rt.py", "content": "x"})
        self.assertEqual(self.calls, [("a_rt.py", "x")])
        store.revoke_rule(rule.id)
        result = gated.invoke({"filepath": "a_rt.py", "content": "x"})
        self.assertIn(REJECT_PHRASE, result, "撤销后同动作必须重新过审批")
        self.assertEqual(self.calls, [("a_rt.py", "x")], "被拒的调用不得再触达工具")
        decisions = [c.kwargs for c in self.audit_mock.log_event.call_args_list
                     if c.kwargs.get("event") == EVENT_APPROVAL_DECISION]
        self.assertEqual([d["source"] for d in decisions],
                         [DecisionSource.RULE_AUTO.value,
                          DecisionSource.UNATTENDED.value])


# ============ 规则文件对 agent 写面不可达(office 外落点的安全前提) ============

from auditronclaw.core.tools.sandbox_tools import build_office_tools


class TestRulesFileUnreachable(unittest.TestCase):
    """agent 经 write_office_file / execute_office_shell 均够不着规则文件。

    规则文件与 office 是兄弟目录(同在 workspace 下);写面被路径校验挡在
    office 内是"规则够不着自己"的全部前提,此处对各类逃逸形态逐一钉死。
    """

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="rules_unreachable_test_")
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        # office 与规则文件互为兄弟,复刻生产布局(workspace/office + workspace/approval_rules.json)
        self.office_dir = os.path.join(self.tmp_dir, "office")
        os.makedirs(self.office_dir, exist_ok=True)
        self.rules_path = os.path.join(self.tmp_dir, "approval_rules.json")
        # 工位落点经工厂闭包注入(05 票):工具绑定本测试的 office 布局
        office_tools = {t.name: t for t in build_office_tools(self.office_dir)}
        self.write_office_file = office_tools["write_office_file"]
        self.execute_office_shell = office_tools["execute_office_shell"]

    def assert_rejected_and_absent(self, result, attempted_path):
        """拒绝话术到位 + 目标文件确实没被写出"""
        self.assertIn("越权", result, f"逃逸写必须被路径校验拒绝:{result}")
        self.assertFalse(os.path.exists(attempted_path), "被拒的写不得落盘")

    def test_write_tool_cannot_reach_rules_file(self):
        """写工具:上跳/office 前缀拼上跳/绝对路径,三种形态全拒"""
        for filepath in ("../approval_rules.json",
                         "office/../approval_rules.json",
                         self.rules_path):
            with self.subTest(filepath=filepath):
                result = self.write_office_file.invoke({"filepath": filepath, "content": "pwned"})
                self.assert_rejected_and_absent(result, self.rules_path)

    def test_sibling_prefix_escape_closed(self):
        """同前缀兄弟名不是 office 内路径:../office_x 不得经 startswith 漏出"""
        sibling = os.path.join(self.tmp_dir, "office_sibling.txt")
        result = self.write_office_file.invoke(
            {"filepath": "../office_sibling.txt", "content": "pwned"})
        self.assert_rejected_and_absent(result, sibling)

    def test_shell_tool_cannot_reach_rules_file(self):
        """shell 工具:重定向目标与读参数的上跳全拒,文件不被写不被读"""
        for command in ("echo pwned > ../approval_rules.json",
                        "echo pwned > ..\\approval_rules.json",
                        "type ../approval_rules.json"):
            with self.subTest(command=command):
                result = self.execute_office_shell.invoke({"command": command})
                self.assertIn("拒绝", result, f"逃逸命令必须被拒:{result}")
        self.assertFalse(os.path.exists(self.rules_path))

    def test_legitimate_write_inside_office_still_works(self):
        """守门不误伤:office 内正常写不受影响(前缀修复的对照组)"""
        inner = os.path.join(self.office_dir, "inner_ok.txt")
        result = self.write_office_file.invoke({"filepath": "inner_ok.txt", "content": "ok"})
        self.assertIn("成功", result)
        self.assertTrue(os.path.exists(inner))


# ============ 装配点接线:create_agent_app 挂真实规则匹配 ============

from contextlib import ExitStack

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval.gate import REJECT_PHRASE


class _ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_session_engine)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽"
        return self.script.pop(0)


class TestAssemblyWiresRules(unittest.TestCase):
    """create_agent_app 构造 RuleStore 并把 matcher 交给门:规则文件驱动放行。"""

    def _workspace_for(self, rules_path):
        """规则文件落点随 workspace 注入(05 票):以规则文件所在目录为工作区根。"""
        from auditronclaw.core.config import WorkspaceConfig
        workspace = WorkspaceConfig.from_root(os.path.dirname(rules_path))
        workspace.ensure_dirs()
        return workspace

    def _app_bound_tools(self, stack, llm, workspace):
        from auditronclaw.core.agent import create_agent_app
        stack.enter_context(patch('auditronclaw.core.agent.get_provider', return_value=llm))
        stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]))
        create_agent_app(provider_name="fake", model_name="fake-model",
                         workspace=workspace,
                         checkpointer=MemorySaver(), thread_id="wire_test")
        return llm.bound

    def test_empty_rules_file_keeps_unattended_rejection(self):
        """空规则集:装配点接了 matcher,门照常拒绝并继续(冷启动形态)"""
        tmp_dir = tempfile.mkdtemp(prefix="wire_empty_test_")
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        llm = _ScriptedLLM([AIMessage(content="done")])
        with ExitStack() as stack:
            stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
            workspace = self._workspace_for(
                os.path.join(tmp_dir, "approval_rules.json"))
            bound = self._app_bound_tools(stack, llm, workspace)
            gated = next(t for t in bound if t.name == "write_office_file")
            result = gated.invoke({"filepath": "wire_probe.py", "content": "x"})
        self.assertIn(REJECT_PHRASE, result)

    def test_seeded_rule_lets_matching_call_pass(self):
        """预置规则文件:同动作同作用域的调用经装配点放行(文件驱动,非注入)"""
        tmp_dir = tempfile.mkdtemp(prefix="wire_seeded_test_")
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        rules_path = os.path.join(tmp_dir, "approval_rules.json")
        with open(rules_path, "w", encoding="utf-8") as f:
            json.dump([{"id": "seed1", "action": "write",
                        "scope": "office/wire_zone/**", "source": "bench_fixture",
                        "created_at": "2026-08-27T00:00:00Z"}], f)
        llm = _ScriptedLLM([AIMessage(content="done")])
        with ExitStack() as stack:
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            workspace = self._workspace_for(rules_path)
            bound = self._app_bound_tools(stack, llm, workspace)
            gated = next(t for t in bound if t.name == "write_office_file")
            # 作用域内:放行并真写(写进临时工作区 office 的 wire_zone/,随根清理)
            probe = os.path.join(workspace.office_dir, "wire_zone", "seeded.py")
            result_in = gated.invoke({"filepath": "wire_zone/seeded.py", "content": "x"})
            self.assertNotIn(REJECT_PHRASE, result_in)
            self.assertTrue(os.path.exists(probe), "规则命中的写必须真实落盘")
            # 作用域外:照拒(分界线在装配点同样成立)
            result_out = gated.invoke({"filepath": "outside_zone.py", "content": "x"})
            self.assertIn(REJECT_PHRASE, result_out)
        decisions = [c.kwargs for c in audit_mock.log_event.call_args_list
                     if c.kwargs.get("event") == EVENT_APPROVAL_DECISION]
        self.assertEqual([d["source"] for d in decisions],
                         [DecisionSource.RULE_AUTO.value,
                          DecisionSource.UNATTENDED.value])


if __name__ == '__main__':
    unittest.main()
