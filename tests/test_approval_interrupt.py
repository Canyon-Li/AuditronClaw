"""审批门 03 票:打断机制(interrupt+resume)、回合来源、审批超时。

分层(沿用仓库测试纪律):
- 类型单测:ApprovalRequest 字段定稿 / ApprovalDecision 答案类型(source 与审计
  共用 DecisionSource)/ TurnOrigin 回合来源枚举
- 假 LLM 驱动引擎测试:人来源回合 interrupt→应答→Command resume 放行/拒绝/
  永久允许铸规则/超时即拒;心跳来源构造上永不 interrupt;TOCTOU(批准后参数
  变更即拒);文本前缀标记不再影响来源判定
- 基准应答档位哨兵:bench_pipeline 不消费逐事件流,门在缺省(无人)形态
  永不打断;有人档(06 票)经 attended 参数显式开启,行为测试在
  tests/test_bench_approval_fixture.py

词汇见 CONTEXT.md「审批门/审批规则」。假件先例:tests/test_session_engine.py。
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval.gate import (
    DecisionSource,
    EVENT_APPROVAL_DECISION,
    EVENT_APPROVAL_REQUESTED,
    EVENT_RULE_PERSISTED,
    REJECT_PHRASE,
    ApprovalDecision,
    TurnOrigin,
    ensure_decision,
)
from auditronclaw.core.bus import TurnRequest
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    SessionEngine,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnEvent,
)


# ============ 类型定稿:接口载荷是第三章 Web 的复用面,字段不漂移 ============

class TestApprovalInterfaceTypes(unittest.TestCase):
    """ApprovalRequest / ApprovalDecision / TurnOrigin 类型钉子。"""

    def test_approval_request_fields_finalized(self):
        """ApprovalRequest 四字段定稿,是 frozen 的回合事件"""
        req = ApprovalRequest(tool="write_office_file",
                              args={"filepath": "a.py", "content": "x"},
                              risk_class="write", reason="写类副作用")
        self.assertIsInstance(req, TurnEvent)
        self.assertEqual(req.tool, "write_office_file")
        self.assertEqual(req.args, {"filepath": "a.py", "content": "x"})
        self.assertEqual(req.risk_class, "write")
        self.assertEqual(req.reason, "写类副作用")
        with self.assertRaises(Exception):
            req.tool = "other"  # frozen:载荷不可中途改写

    def test_approval_decision_fields_and_shared_source(self):
        """ApprovalDecision 三字段;source 与审计事件共用同一 DecisionSource 枚举"""
        d = ApprovalDecision(approved=True, persist=False,
                             source=DecisionSource.USER_ONCE)
        self.assertIs(d.approved, True)
        self.assertIs(d.persist, False)
        self.assertIs(d.source, DecisionSource.USER_ONCE, "source 必须就是审计那个枚举")

    def test_turn_origin_enum_values(self):
        """回合来源四值:human 可问人;heartbeat/bench/unattended 构造上不问人"""
        self.assertEqual({o.value for o in TurnOrigin},
                         {"human", "heartbeat", "bench", "unattended"})

    def test_ensure_decision_fail_closed(self):
        """应答值校验:合规原样放行;不合规(垃圾值/伪造 source)一律按无人拒"""
        ok = ApprovalDecision(True, True, DecisionSource.USER_PERSIST)
        self.assertIs(ensure_decision(ok), ok)
        for bad in (None, "yes", True, {"approved": True},
                    ApprovalDecision(True, False, "user_once")):  # source 伪造字符串
            with self.subTest(bad=bad):
                got = ensure_decision(bad)
                self.assertIs(got.approved, False)
                self.assertIs(got.persist, False)
                self.assertIs(got.source, DecisionSource.UNATTENDED)

    def test_turn_request_envelope(self):
        """队列信封 TurnRequest:text + 类型化来源(frozen)"""
        item = TurnRequest(text="hi", origin=TurnOrigin.HEARTBEAT)
        self.assertEqual(item.text, "hi")
        self.assertIs(item.origin, TurnOrigin.HEARTBEAT)
        with self.assertRaises(Exception):
            item.origin = TurnOrigin.HUMAN  # frozen:来源不可事后改写

    def test_timeout_config_default_and_env(self):
        """审批超时默认 5 分钟(300 秒),可用 AUDITRONCLAW_APPROVAL_TIMEOUT 覆盖"""
        from auditronclaw.core.session import SessionEngine
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AUDITRONCLAW_APPROVAL_TIMEOUT", None)
            self.assertEqual(SessionEngine(None, "probe").approval_timeout, 300.0)
        code = (
            "import sys; sys.path.insert(0, r'%s')\n"
            "from auditronclaw.core.session import SessionEngine\n"
            "print(SessionEngine(None, 'probe').approval_timeout)\n"
        ) % os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        env = {**os.environ, "AUDITRONCLAW_APPROVAL_TIMEOUT": "1.5"}
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(float(result.stdout.strip().splitlines()[-1]), 1.5)


# ============ 假件:脚本化 LLM + 高危桩工具(零真实文件系统副作用) ============

def _hazard_write_stub(calls: list) -> StructuredTool:
    """名为 write_office_file 的桩:分级按名入册(write 级),执行只记录不落盘。"""
    def run(filepath: str, content: str, mode: str = "w") -> str:
        calls.append(dict(filepath=filepath, content=content, mode=mode))
        return f"written:{filepath}"
    return StructuredTool.from_function(
        func=run, name="write_office_file", description="测试桩:写工具")


def _hazard_delete_stub(calls: list) -> StructuredTool:
    """名为 delete_scheduled_task 的桩:delete 级,执行只记录。"""
    def run(task_id: str) -> str:
        calls.append(dict(task_id=task_id))
        return f"deleted:{task_id}"
    return StructuredTool.from_function(
        func=run, name="delete_scheduled_task", description="测试桩:删工具")


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_session_engine)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        return self.script.pop(0)


def _write_call(call_id: str, tool: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": tool, "args": args, "id": call_id, "type": "tool_call"}])


def _build_app(stack: ExitStack, llm, tools):
    """按现有注入点构造 agent app;规则文件钉到临时工作区,返回 (app, workspace)。

    patch 由调用方 stack 持有,须罩住整个运行期(分级/规则匹配在工具调用时
    才发生)。先例:tests/test_approval_gate.py TestUnattendedRejectionContinues。
    """
    from auditronclaw.core.agent import create_agent_app
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="interrupt_ws_"))
    workspace.ensure_dirs()
    stack.enter_context(patch('auditronclaw.core.agent.get_provider', return_value=llm))
    stack.enter_context(patch('auditronclaw.core.agent.build_builtin_tools',
                              return_value=tools))
    stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                              return_value=[]))
    stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
    app = create_agent_app(provider_name="fake", model_name="fake-model",
                           workspace=workspace,
                           checkpointer=MemorySaver(), thread_id="interrupt_test")
    return app, workspace


def _drive(engine: SessionEngine, text: str, origin=None) -> list:
    """跑引擎一个回合,收集全部回合事件。origin=None 表示不传(缺省来源)。"""
    events = []

    async def run():
        if origin is None:
            agen = engine.run_turn(text)
        else:
            agen = engine.run_turn(text, origin=origin)
        async for ev in agen:
            events.append(ev)

    asyncio.run(run())
    return events


# ============ interrupt + resume:人来源回合的问人-续行 ============

APPROVE_ONCE = lambda req: ApprovalDecision(True, False, DecisionSource.USER_ONCE)
DENY_ONCE = lambda req: ApprovalDecision(False, False, DecisionSource.USER_ONCE)


class TestInterruptApproveAndDeny(unittest.TestCase):
    """人来源回合:规则未命中的高危调用 interrupt 问人,应答后 resume 放行/拒绝。"""

    def _run(self, responder, script_tail=None):
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content=script_tail or "已写完。"),
        ]
        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            engine = SessionEngine(app, "interrupt_test",
                                   approval_responder=responder)
            events = _drive(engine, "写一份日报", origin=TurnOrigin.HUMAN)
        return events, calls, audit_mock

    def test_approve_once_executes_exactly_the_approved_call(self):
        """批准一次:放行执行,请求与执行绑定同一份规范化参数"""
        events, calls, audit_mock = self._run(APPROVE_ONCE)

        # 事件流形状:ToolCall → ApprovalRequest → ToolResult → Reply(final) → TurnEnd
        self.assertEqual([type(e) for e in events],
                         [ToolCall, ApprovalRequest, ToolResult, Reply, TurnEnd])
        req = events[1]
        self.assertEqual(req.tool, "write_office_file")
        self.assertEqual(req.args, {"filepath": "reports/daily.md",
                                    "content": "x", "mode": "w"},
                         "请求载荷带 schema 规范化后的完整参数")
        self.assertEqual(req.risk_class, "write")
        self.assertTrue(req.reason)
        self.assertEqual(events[2], ToolResult(tool="write_office_file",
                                                result="written:reports/daily.md"))
        # 执行的就是批准的那份参数(审批与执行绑定同一份规范化调用)
        self.assertEqual(calls, [{"filepath": "reports/daily.md",
                                  "content": "x", "mode": "w"}])
        # 回合轨迹完整(ToolResult 真实结果入轨)
        self.assertEqual(events[-1].trajectory.tool_calls,
                         [{"tool": "write_office_file",
                           "args": {"filepath": "reports/daily.md", "content": "x"}}])
        self.assertEqual(events[-1].trajectory.tool_results,
                         [{"tool": "write_office_file",
                           "result": "written:reports/daily.md"}])

        # 审计成对且仅一对:节点重跑不双写 requested(单点补丁会截获全部审计
        # 事件,审批对按事件名过滤——llm_input 等非审批事件不在比对范围)
        gate_events = [c.kwargs.get("event") for c in audit_mock.log_event.call_args_list
                       if c.kwargs.get("event") in
                       (EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_DECISION)]
        self.assertEqual(gate_events, [EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_DECISION])
        decision = next(c.kwargs for c in audit_mock.log_event.call_args_list
                        if c.kwargs.get("event") == EVENT_APPROVAL_DECISION)
        self.assertIs(decision["approved"], True)
        self.assertEqual(decision["source"], DecisionSource.USER_ONCE.value)

    def test_deny_returns_rejection_as_tool_result_and_turn_finishes(self):
        """拒绝:拒绝话术作为 tool_result 返回,原工具不执行,回合照常收尾"""
        events, calls, audit_mock = self._run(DENY_ONCE)

        self.assertEqual([type(e) for e in events],
                         [ToolCall, ApprovalRequest, ToolResult, Reply, TurnEnd])
        self.assertIn(REJECT_PHRASE, events[2].result)
        self.assertEqual(calls, [], "被拒的调用不得触达原工具")
        self.assertTrue(events[3].final)
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.USER_ONCE.value)

    def test_rule_hit_skips_asking_entirely(self):
        """规则命中不问人:放行且 source=rule_auto,应答器不被调"""
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="完成。"),
        ]
        from auditronclaw.core.approval.rules import RuleStore
        with ExitStack() as stack:
            workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="rule_hit_"))
            workspace.ensure_dirs()
            with open(workspace.approval_rules_file, "w", encoding="utf-8") as f:
                json.dump([{"id": "r1", "action": "write", "scope": "office/reports/**",
                            "source": "approval", "created_at": "2026-08-27T00:00:00Z"}], f)
            stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                                      return_value=ScriptedLLM(script)))
            stack.enter_context(patch('auditronclaw.core.agent.build_builtin_tools',
                                      return_value=[_hazard_write_stub(calls)]))
            stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                                      return_value=[]))
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            from auditronclaw.core.agent import create_agent_app
            app = create_agent_app(provider_name="fake", model_name="fake-model",
                                   workspace=workspace,
                                   checkpointer=MemorySaver(), thread_id="rule_hit_test")
            responder = lambda req: self.fail("规则命中的调用不得问人")
            engine = SessionEngine(app, "rule_hit_test", approval_responder=responder)
            events = _drive(engine, "写日报", origin=TurnOrigin.HUMAN)

        self.assertNotIn(ApprovalRequest, [type(e) for e in events])
        # 事件流:ToolCall → ToolResult(真实执行) → Reply → TurnEnd,无打断
        self.assertEqual(events[1], ToolResult(tool="write_office_file",
                                                result="written:reports/daily.md"))
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertEqual(decision["source"], DecisionSource.RULE_AUTO.value)
        self.assertEqual(decision["rule_id"], "r1")


class TestPersistMintsRule(unittest.TestCase):
    """永久允许:persist=true 铸规则 + rule_persisted 入审计,之后同调用静默。"""

    def test_persist_then_next_call_silent_rule_auto(self):
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="第一回合完成。"),
            _write_call("call_2", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "y"}),
            AIMessage(content="第二回合完成。"),
        ]
        asked = []

        def responder(req: ApprovalRequest):
            asked.append(req)
            return ApprovalDecision(True, True, DecisionSource.USER_PERSIST)

        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            engine = SessionEngine(app, "persist_test", approval_responder=responder)
            first = _drive(engine, "写日报", origin=TurnOrigin.HUMAN)
            second = _drive(engine, "再写一次", origin=TurnOrigin.HUMAN)

        # 第一回合:问了人,永久允许;两回合都真实执行
        self.assertIn(ApprovalRequest, [type(e) for e in first])
        self.assertEqual(len(calls), 2)
        # 第二回合:规则已铸,静默放行,不再问人
        self.assertNotIn(ApprovalRequest, [type(e) for e in second])
        self.assertEqual(len(asked), 1, "永久允许之后同调用不得再问人")

    def test_rule_file_shape_and_audit_after_persist(self):
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="完成。"),
        ]
        with ExitStack() as stack:
            app, workspace = _build_app(stack, ScriptedLLM(script),
                                        [_hazard_write_stub(calls)])
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            # 规则文件路径与 gate 内 RuleStore 同源:都出自装配工作区
            path = workspace.approval_rules_file
            engine = SessionEngine(
                app, "persist_shape_test",
                approval_responder=lambda req: ApprovalDecision(
                    True, True, DecisionSource.USER_PERSIST))
            _drive(engine, "写日报", origin=TurnOrigin.HUMAN)

        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(len(entries), 1, "永久允许铸出恰好一条规则")
        self.assertEqual(entries[0]["action"], "write")
        self.assertEqual(entries[0]["scope"], "office/reports/daily.md")
        self.assertEqual(entries[0]["source"], "approval")
        # 审计:requested → decision(user_persist) → rule_persisted
        gate_events = [c.kwargs.get("event") for c in audit_mock.log_event.call_args_list
                       if c.kwargs.get("event") in
                       (EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_DECISION,
                        EVENT_RULE_PERSISTED)]
        self.assertEqual(gate_events,
                         [EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_DECISION,
                          EVENT_RULE_PERSISTED])
        decision = next(c.kwargs for c in audit_mock.log_event.call_args_list
                        if c.kwargs.get("event") == EVENT_APPROVAL_DECISION)
        self.assertEqual(decision["source"], DecisionSource.USER_PERSIST.value)


# ============ 超时:挂起的审批到期即终局拒,单 worker 队列不被堵死 ============

class TestApprovalTimeout(unittest.TestCase):
    """审批等待超时:短超时注入下,回合必须收尾,拒绝+审计 source=timeout。"""

    def test_timeout_denies_and_turn_completes(self):
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="未获批准,已放弃。"),
        ]

        async def hang(req):
            await asyncio.sleep(30)  # 永不应答:模拟操作员离开

        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            engine = SessionEngine(app, "timeout_test",
                                   approval_responder=hang, approval_timeout=0.05)
            start = time.monotonic()
            events = _drive(engine, "写日报", origin=TurnOrigin.HUMAN)
            elapsed = time.monotonic() - start

        self.assertLess(elapsed, 10, "短超时下回合必须快速收尾,不挂死")
        self.assertEqual(calls, [], "超时=拒绝,原工具不得执行")
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertEqual(len(results), 1)
        self.assertIn(REJECT_PHRASE, results[0].result)
        self.assertIsInstance(events[-1], TurnEnd, "回合照常发 TurnEnd(队列可继续)")
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.TIMEOUT.value)

    def test_misbehaving_responder_denies_fail_closed(self):
        """应答器返回垃圾值或抛异常:一律按无人拒,回合收尾不出错"""
        for responder in (lambda req: "yes",  # 非法载荷
                          lambda req: (_ for _ in ()).throw(RuntimeError("适配器故障"))):
            with self.subTest(responder=responder), ExitStack() as stack:
                calls = []
                script = [
                    _write_call("call_1", "write_office_file",
                                {"filepath": "reports/daily.md", "content": "x"}),
                    AIMessage(content="已放弃。"),
                ]
                app, _workspace = _build_app(stack, ScriptedLLM(script),
                                             [_hazard_write_stub(calls)])
                engine = SessionEngine(app, "misbehave_test",
                                       approval_responder=responder)
                events = _drive(engine, "写日报", origin=TurnOrigin.HUMAN)
                self.assertEqual(calls, [], "不可信应答不得放行")
                self.assertIsInstance(events[-1], TurnEnd)

    def test_no_responder_human_origin_denies_immediately(self):
        """无人应答通道的人来源回合:立即拒(不等到超时),fail-closed"""
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已放弃。"),
        ]
        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            engine = SessionEngine(app, "no_responder_test",
                                   approval_timeout=30)
            start = time.monotonic()
            events = _drive(engine, "写日报", origin=TurnOrigin.HUMAN)
            elapsed = time.monotonic() - start

        self.assertLess(elapsed, 5, "无应答通道时不得等满超时才拒")
        self.assertEqual(calls, [])
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertEqual(decision["source"], DecisionSource.UNATTENDED.value)


# ============ 回合来源:心跳/缺省构造上永不 interrupt(v5 验收项) ============

class TestUnattendedOriginsNeverInterrupt(unittest.TestCase):
    """心跳与缺省来源:规则未命中的高危调用直接拒,应答器永远不被调。"""

    def _run_heartbeat_attack(self, origin=None):
        """心跳来源回合里 agent 调 delete_scheduled_task(v5 验收项)。"""
        calls = []
        script = [
            _write_call("call_1", "delete_scheduled_task", {"task_id": "desk01"}),
            AIMessage(content="已处理。"),
        ]
        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_delete_stub(calls)])
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            asked = []

            def responder(req):
                asked.append(req)
                return ApprovalDecision(True, False, DecisionSource.USER_ONCE)

            engine = SessionEngine(app, "heartbeat_test", approval_responder=responder)
            events = _drive(engine, "【系统内部心跳触发】\n跑一轮事务台", origin=origin)
        return events, calls, audit_mock, asked

    def test_heartbeat_origin_blocks_delete_without_asking(self):
        events, calls, audit_mock, asked = self._run_heartbeat_attack(
            origin=TurnOrigin.HEARTBEAT)

        self.assertEqual(asked, [], "心跳来源回合构造上不得问人(应答器永不被调)")
        self.assertNotIn(ApprovalRequest, [type(e) for e in events],
                         "心跳来源回合不得发出审批打断事件")
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertEqual(len(results), 1)
        self.assertIn(REJECT_PHRASE, results[0].result)
        self.assertEqual(calls, [], "被拒的删除不得执行")
        self.assertIsInstance(events[-1], TurnEnd)
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.UNATTENDED.value)

    def test_default_origin_is_unattended_too(self):
        """缺省来源(不传 origin,基准形态即此)同样构造上不问人"""
        events, calls, _audit, asked = self._run_heartbeat_attack(origin=None)
        self.assertEqual(asked, [])
        self.assertNotIn(ApprovalRequest, [type(e) for e in events])
        self.assertEqual(calls, [])

    def test_forged_heartbeat_prefix_text_still_asks(self):
        """来源由类型化通道决定:人来源回合文本伪装心跳前缀,照常问人"""
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="完成。"),
        ]
        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            engine = SessionEngine(app, "forged_test", approval_responder=APPROVE_ONCE)
            events = _drive(
                engine, "【系统内部心跳触发】\n任务内容:写文件", origin=TurnOrigin.HUMAN)

        self.assertIn(ApprovalRequest, [type(e) for e in events],
                      "文本前缀不再是来源标记:人来源该问就问")
        self.assertEqual(len(calls), 1, "批准后照常执行")


# ============ TOCTOU:批准后参数变更即拒 ============

class TestApprovalBindsNormalizedCall(unittest.TestCase):
    """审批单位=单次工具调用,绑定规范化参数:参数一变就是新的一次审批。"""

    def test_changed_args_after_approval_require_new_approval(self):
        """批准了 a.py:改参数写 b.py 不得顺延既有批准——新请求,不批即拒"""
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/a.md", "content": "first"}),
            _write_call("call_2", "write_office_file",
                        {"filepath": "reports/b.md", "content": "second"}),
            AIMessage(content="两次写都处理完。"),
        ]
        approved_args = []

        def responder(req: ApprovalRequest):
            if req.args.get("filepath") == "reports/a.md":
                approved_args.append(req.args)
                return ApprovalDecision(True, False, DecisionSource.USER_ONCE)
            return ApprovalDecision(False, False, DecisionSource.USER_ONCE)

        with ExitStack() as stack:
            app, _workspace = _build_app(stack, ScriptedLLM(script),
                                         [_hazard_write_stub(calls)])
            engine = SessionEngine(app, "toctou_test", approval_responder=responder)
            events = _drive(engine, "先写 a 再写 b", origin=TurnOrigin.HUMAN)

        # 两次独立审批请求,参数各不同
        requests = [e for e in events if isinstance(e, ApprovalRequest)]
        self.assertEqual([r.args.get("filepath") for r in requests],
                         ["reports/a.md", "reports/b.md"])
        # 执行的恰好是被批准的那份参数(绑定同一份规范化调用)
        self.assertEqual([c["filepath"] for c in calls], ["reports/a.md"],
                         "批准的参数与执行的参数必须同一份;b.md 未获批不得执行")
        self.assertEqual(calls[0], dict(approved_args[0]),
                         "工具收到的参数就是请求载荷里那份")
        # b 的拒绝作为 tool_result 返回,回合收尾
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertEqual(len(results), 2)
        self.assertIn("written:reports/a.md", results[0].result)
        self.assertIn(REJECT_PHRASE, results[1].result)
        self.assertIsInstance(events[-1], TurnEnd)


# ============ 基准应答档位哨兵 ============

class TestBenchZeroChangeSentinel(unittest.TestCase):
    """bench_pipeline 不消费逐事件流:门在缺省(无人)形态永不打断。

    06 票基准应答档位后契约更新:golden 档(有人且都批)经 attended 参数
    显式开启——回合来源与应答器由档位注入,打断-续行仍全在引擎内部;
    injection 档恒为缺省无人形态。档位行为测试见
    tests/test_bench_approval_fixture.py。
    """

    def test_bench_pipeline_source_untouched(self):
        """源码哨兵:基准不得亲手消费打断/续行/审批事件(那归引擎)"""
        import bench_pipeline
        src = Path(bench_pipeline.__file__).read_text(encoding="utf-8")
        for banned in ("ApprovalRequest", "Command", "interrupt"):
            self.assertNotIn(banned, src,
                             f"bench_pipeline 出现 {banned!r}:打断-续行须留在会话引擎")

    def test_default_origin_turn_completes_with_gate_rejection(self):
        """缺省来源端到端:高危调用拒绝并继续,不挂起、不问人"""
        calls = []
        script = [
            _write_call("call_1", "write_office_file",
                        {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已收尾。"),
        ]
        case = {"id": "sentinel", "surface": "bench", "trigger": "写一份日报"}
        import bench_pipeline
        with ExitStack() as stack:
            stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                                      return_value=ScriptedLLM(script)))
            stack.enter_context(patch('auditronclaw.core.agent.build_builtin_tools',
                                      return_value=[_hazard_write_stub(calls)]))
            stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                                      return_value=[]))
            workspace = WorkspaceConfig.from_root(
                tempfile.mkdtemp(prefix="sentinel_ws_"))
            workspace.ensure_dirs()
            raw = asyncio.run(bench_pipeline._drive_agent(
                case, workspace, "fake-model", "fake", "sentinel_bench", []))

        self.assertEqual(calls, [])
        self.assertEqual(len(raw["tool_results"]), 1)
        self.assertIn(REJECT_PHRASE, raw["tool_results"][0]["result"])
        self.assertEqual(raw["reply"], "已收尾。")


if __name__ == '__main__':
    unittest.main()
