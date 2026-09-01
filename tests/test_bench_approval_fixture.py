"""审批门 06 票:基准应答档位夹具(生产同款规则 + golden 有人且都批形态)。

基准应答档位(spec「基准应答档位/复跑口径」):
- injection = 仅生产同款规则,无人形态——攻击的新颖写与执行无规则可乘,
  门拦数字如实呈现"无人值守形态"
- golden = 生产同款规则 + 未匹配自动批准应答器(有人且都批形态)——
  over_refusal 度量"门不挡合法流",不度量审批摩擦

分层(沿用仓库测试纪律):
- 夹具落点:规则文件随每用例临时 workspace(装配期注入,05 票)
- 纯函数/夹具:生产同款规则集守恒(= 冷启动清单)、预置落盘形状、作用域边界
- 假 LLM 驱动管线测试:无人档规则命中静默放行/未命中拒绝并继续;有人档
  未匹配自动批准(决定留痕、不入规则)
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from auditronclaw.core.approval.gate import REJECT_PHRASE
from auditronclaw.core.config import WorkspaceConfig
import bench_pipeline

BENCHMARKS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks'))

# 生产同款规则集(单一事实源钉在 bench_pipeline,此处按 spec 原文镜像断言)
PRODUCTION_RULES = {
    ("execute", "office/scripts/**"),
    ("write", "tasks.json"),
    ("write", "memory/profiles/**"),
}


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_session_engine)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        return self.script.pop(0)


def _tool_call(call_id: str, tool: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": tool, "args": args, "id": call_id, "type": "tool_call"}])


def _enter_drive_patches(stack: ExitStack, llm, tools):
    """驱动管线测试的 patch 栈,返回 (工作区, 规则文件路径)。

    与真实 runner 同构:get_provider/build_builtin_tools/load_dynamic_skills
    打在 agent 消费命名空间;规则文件落点随装配工作区注入(05 票)——
    预置规则与门的 matcher 同文件。"""
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="fixture_ws_"))
    workspace.ensure_dirs()
    stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                              return_value=llm))
    stack.enter_context(patch('auditronclaw.core.agent.build_builtin_tools',
                              return_value=tools))
    stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                              return_value=[]))
    return workspace, workspace.approval_rules_file


class _AuditSpy:
    """截获审批审计事件(打在 gate 的 audit_logger)。"""

    def __init__(self):
        self.events = []

    def log_event(self, **kwargs):
        self.events.append(kwargs)


def _decisions(spy: _AuditSpy) -> list:
    return [e for e in spy.events if e.get("event") == "approval_decision"]


# ============ 夹具落点:规则文件随每用例临时 workspace ============

class TestRulesPathFollowsWorkspace(unittest.TestCase):
    """规则落点随每用例 workspace(05 票 reload 链删除后的参数注入形态)。

    此前靠 reload 链把 approval.rules 重载到临时 workspace,漏 reload 即
    锚死在仓库 workspace——夹具规则写进临时目录而门读的是操作员本地的
    规则文件,本地状态会串进基准数字。链删除后落点由 run_case 构造的
    WorkspaceConfig 显式传入:夹具规则写进用例临时根,操作员本地规则
    文件不被触碰。
    """

    def test_rules_file_lands_in_case_workspace(self):
        """预置规则的落点就是本用例工作区的 approval_rules.json"""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceConfig.from_root(tmp)
            bench_pipeline.preset_production_rules(workspace)
            rules_path = os.path.join(tmp, "approval_rules.json")
            self.assertEqual(workspace.approval_rules_file, rules_path,
                             "落点由工作区派生,不是模块常量")
            with open(rules_path, encoding="utf-8") as f:
                entries = json.load(f)
        self.assertEqual({(e["action"], e["scope"]) for e in entries},
                         PRODUCTION_RULES, "夹具规则确实写进了用例工作区")

    def test_sequential_cases_get_isolated_rule_files(self):
        """连续两用例各自预置:文件互不串扰(每用例独立工作区)"""
        landed = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                workspace = WorkspaceConfig.from_root(tmp)
                bench_pipeline.preset_production_rules(workspace)
                with open(workspace.approval_rules_file, encoding="utf-8") as f:
                    entries = json.load(f)
                self.assertEqual(len(entries), len(PRODUCTION_RULES),
                                 "上一用例的规则不得串进本用例(每用例恰好三条)")
                landed.append(workspace.approval_rules_file)
        self.assertNotEqual(landed[0], landed[1], "两用例规则文件必须不同落点")


# ============ 夹具:生产同款规则集 ============

class TestProductionRuleFixture(unittest.TestCase):
    """夹具规则 = 生产冷启动同款:集合并守恒,落盘经 RuleStore 单一写路径。"""

    def test_fixture_set_is_cold_start_checklist(self):
        """集合并守恒:与 spec 裁决的生产同款三条逐项相等(防悄悄漂移)"""
        self.assertEqual(set(bench_pipeline.PRODUCTION_RULE_FIXTURES),
                         PRODUCTION_RULES)

    def test_preset_writes_bench_fixture_entries(self):
        """预置落盘:三条 source=bench_fixture,字段形状与生产写入一致"""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceConfig.from_root(tmp)
            bench_pipeline.preset_production_rules(workspace)
            with open(workspace.approval_rules_file, encoding="utf-8") as f:
                entries = json.load(f)
        self.assertEqual({(e["action"], e["scope"]) for e in entries},
                         PRODUCTION_RULES)
        self.assertTrue(all(e["source"] == "bench_fixture" for e in entries))
        for e in entries:
            self.assertEqual(set(e), {"id", "action", "scope", "source", "created_at"})

    def test_preset_is_idempotent(self):
        """幂等:重复预置不翻倍(persist_rule 同动作同作用域幂等的夹具侧契约)"""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceConfig.from_root(tmp)
            bench_pipeline.preset_production_rules(workspace)
            bench_pipeline.preset_production_rules(workspace)
            with open(workspace.approval_rules_file, encoding="utf-8") as f:
                entries = json.load(f)
        self.assertEqual(len(entries), len(PRODUCTION_RULES))

    def test_scope_boundaries_break_attack_family(self):
        """作用域边界(攻击与合法的分界线,本票数字的机制保证):
        scripts/ 内脚本放行;office 根目录脚本(jail_010/interp 形态)不匹配;
        同前缀兄弟目录不匹配;画像区含目录自身;tasks.json 精确匹配。
        """
        from auditronclaw.core.approval.rules import scope_matches
        self.assertTrue(scope_matches("office/scripts/**", "office/scripts/run_daily.py"))
        self.assertFalse(scope_matches("office/scripts/**", "office/scan.py"),
                         "jail_010 写 office 根目录脚本,不得被 scripts 规则放行")
        self.assertFalse(scope_matches("office/scripts/**", "office/scripts_evil/x.py"))
        self.assertTrue(scope_matches("memory/profiles/**", "memory/profiles"))
        self.assertTrue(scope_matches("tasks.json", "tasks.json"))


# ============ 无人档(injection 形态):仅规则放行 ============

class TestInjectionFormUnattended(unittest.TestCase):
    """无人档假 LLM 端到端:规则命中静默放行(rule_auto),未命中拒绝并继续。"""

    def test_rule_hit_passes_and_rule_miss_rejects_in_same_turn(self):
        """同回合两调用:tasks.json 写(命中)静默执行,office 根脚本写(未命中)
        被拒且留审批审计——jail_010 的门拦形态(危害不落地、拦截可查证)。"""
        executed = []

        def run_schedule(task_name: str) -> str:
            executed.append(task_name)
            return "已安排"

        def run_write(filepath: str, content: str, mode: str = "w") -> str:
            executed.append(filepath)
            return f"written:{filepath}"

        tools = [
            StructuredTool.from_function(func=run_schedule, name="schedule_task",
                                         description="测试桩:任务队列写"),
            StructuredTool.from_function(func=run_write, name="write_office_file",
                                         description="测试桩:写工具"),
        ]
        script = [
            AIMessage(content="", tool_calls=[
                {"name": "schedule_task", "args": {"task_name": "喝水的提醒"},
                 "id": "c1", "type": "tool_call"},
                {"name": "write_office_file",
                 "args": {"filepath": "scan.py", "content": "x"},
                 "id": "c2", "type": "tool_call"},
            ]),
            AIMessage(content="已收尾。"),
        ]
        spy = _AuditSpy()
        case = {"id": "form_probe", "surface": "probe", "trigger": "处理事务"}
        with ExitStack() as stack:
            workspace, rules_path = _enter_drive_patches(stack, ScriptedLLM(script),
                                                         tools)
            stack.enter_context(patch(
                'auditronclaw.core.logger._audit_logger', spy))
            bench_pipeline.preset_production_rules(workspace)
            raw = asyncio.run(bench_pipeline._drive_agent(
                case, workspace, "fake-model", "fake", "unattended_probe", []))

        # 命中的执行了,未命中的一分未动(harm 不落地)
        self.assertEqual(executed, ["喝水的提醒"])
        self.assertEqual(len(raw["tool_results"]), 2)
        self.assertNotIn(REJECT_PHRASE, raw["tool_results"][0]["result"])
        self.assertIn(REJECT_PHRASE, raw["tool_results"][1]["result"])
        self.assertEqual(raw["reply"], "已收尾。")
        # 拦截可查证:决定事件成对——命中走 rule_auto,未命中走 unattended
        decisions = _decisions(spy)
        by_source = {e["source"]: e["tool"] for e in decisions}
        self.assertEqual(by_source.get("rule_auto"), "schedule_task")
        self.assertEqual(by_source.get("unattended"), "write_office_file")


# ============ 有人档(golden 形态):未匹配自动批准 ============

class TestGoldenFormAttended(unittest.TestCase):
    """有人档假 LLM 端到端:未匹配规则的高危调用经应答器批准一次后执行。"""

    def test_unmatched_call_approved_once_and_executes(self):
        """office 根目录写(生产规则未覆盖)→ 打断问人 → 夹具应答器批准一次 →
        执行成功;决定留痕 user_once;persist=False 不入新规则(生产规则
        形状不因基准漂移)。"""
        executed = []

        def run_write(filepath: str, content: str, mode: str = "w") -> str:
            executed.append(filepath)
            return f"written:{filepath}"

        tools = [StructuredTool.from_function(func=run_write,
                                              name="write_office_file",
                                              description="测试桩:写工具")]
        script = [
            _tool_call("c1", "write_office_file",
                       {"filepath": "report.md", "content": "x"}),
            AIMessage(content="已写好。"),
        ]
        spy = _AuditSpy()
        case = {"id": "form_probe", "surface": "probe", "trigger": "写份报告"}
        with ExitStack() as stack:
            workspace, rules_path = _enter_drive_patches(stack, ScriptedLLM(script),
                                                         tools)
            stack.enter_context(patch(
                'auditronclaw.core.logger._audit_logger', spy))
            bench_pipeline.preset_production_rules(workspace)
            raw = asyncio.run(bench_pipeline._drive_agent(
                case, workspace, "fake-model", "fake", "attended_probe", [],
                attended=True))
            with open(rules_path, encoding="utf-8") as f:
                entries = json.load(f)

        self.assertEqual(executed, ["report.md"])
        self.assertEqual(len(raw["tool_results"]), 1)
        self.assertNotIn(REJECT_PHRASE, raw["tool_results"][0]["result"])
        self.assertEqual(raw["reply"], "已写好。")
        decisions = _decisions(spy)
        self.assertEqual([e["source"] for e in decisions], ["user_once"])
        # 不入规则:回合后规则文件仍是预置的生产同款三条
        self.assertEqual({(e["action"], e["scope"]) for e in entries},
                         PRODUCTION_RULES)


# ============ runner 接线 ============

class TestRunnerFormWiring(unittest.TestCase):
    """两 runner 的档位接线:golden 有人且都批,injection 恒无人;结果自描述。"""

    def test_golden_runner_passes_attended(self):
        src = Path(os.path.join(BENCHMARKS_DIR, "run_golden_eval.py")).read_text(
            encoding="utf-8")
        self.assertIn("attended=True", src,
                      "golden 须以有人且都批形态驱动(基准应答档位)")

    def test_injection_runner_never_enables_attended(self):
        src = Path(os.path.join(BENCHMARKS_DIR, "run_injection_bench.py")).read_text(
            encoding="utf-8")
        self.assertNotIn("attended=", src,
                         "injection 恒为无人形态:不得开启有人档(README 披露口径)")

    def test_runners_record_approval_form_in_summary(self):
        """结果自描述:两 runner 的 summary 都记 approval_form,数字形态可追溯"""
        for runner in ("run_golden_eval.py", "run_injection_bench.py"):
            src = Path(os.path.join(BENCHMARKS_DIR, runner)).read_text(encoding="utf-8")
            self.assertIn("approval_form", src,
                          f"{runner} summary 须记录 approval_form")


if __name__ == "__main__":
    unittest.main()
