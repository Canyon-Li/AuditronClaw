"""Web 审批动线(07 票):decision 帧接线、应答桥、真实栈端到端。

分层(沿用仓库测试纪律):
- 纯函数:decision 帧 choice → ApprovalDecision 三选映射(与 TUI y/a/n
  同源:deny 是人明确拒绝,source=user_once),无效值 None(由 WS 层回
  decision_invalid)
- 应答桥:responder 同步注册挂起 / answer 回填续行 / 终局(超时取消)
  后迟到帧弃置——不答即拒由引擎超时兜底,桥只递送不裁决
- 真实栈端到端:假 LLM + 真门真 interrupt 经 WS 的完整审批闭环——
  批准一次 / 拒绝(话术返回 agent)/ 永久允许(入规则后同调用静默
  rule_auto)/ 不答即拒(引擎超时,TIMEOUT 留痕,回合收口)/ 心跳回合
  构造上不问人(呈现层复验的服务端锚点)

WS 层(stub 引擎)的 decision 帧受理测试在 tests/test_web_ws.py。
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from auditronclaw.core.approval.gate import (
    DecisionSource,
    REJECT_PHRASE,
    ApprovalDecision,
)
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.session import ApprovalRequest

from entry.web import create_web_app
from entry.web_approval import WebApprovalBridge, parse_decision_choice

REQ = ApprovalRequest(tool="run_office_command", args={"command": "rm x"},
                      risk_class="execute", reason="解释器执行")

TOKEN = "probe-token-0123456789abcdef"


# ============ 纯函数:choice 三选映射 ============

class TestParseDecisionChoice(unittest.TestCase):
    """decision 帧 choice → ApprovalDecision:三选映射与 TUI y/a/n 同义。"""

    def test_three_choices_map_to_decisions(self):
        cases = {
            "once": (True, False, DecisionSource.USER_ONCE),
            "always": (True, True, DecisionSource.USER_PERSIST),
            "deny": (False, False, DecisionSource.USER_ONCE),
        }
        for choice, (approved, persist, source) in cases.items():
            with self.subTest(choice=choice):
                decision = parse_decision_choice(choice)
                self.assertIs(decision.approved, approved)
                self.assertIs(decision.persist, persist)
                self.assertIs(decision.source, source,
                              "source 与审计共用 DecisionSource")

    def test_invalid_choices_return_none(self):
        for bad in (None, "yes", "", "ONCE", "once ", 1, True, {}):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_decision_choice(bad),
                                  "无效 choice 是没听懂,不是默认拒")


# ============ 应答桥:挂起/回填/终局弃置 ============

class TestWebApprovalBridge(unittest.TestCase):

    def test_answer_without_pending_is_false(self):
        async def scenario():
            bridge = WebApprovalBridge()
            return bridge.answer(ApprovalDecision(
                approved=False, persist=False, source=DecisionSource.USER_ONCE))

        self.assertFalse(asyncio.run(scenario()),
                         "无挂起审批的 decision 帧无物可答")

    def test_responder_registers_before_first_await_and_resolves(self):
        """同步注册:responder() 调用返回时挂起已入槽(广播-应答竞态关门)。"""
        async def scenario():
            bridge = WebApprovalBridge()
            fut = bridge.responder(REQ)
            self.assertIs(bridge.pending, REQ,
                          "注册同步完成,不等事件环下一拍")
            decision = ApprovalDecision(approved=True, persist=False,
                                        source=DecisionSource.USER_ONCE)
            self.assertTrue(bridge.answer(decision))
            return await fut

        self.assertTrue(asyncio.run(scenario()).approved)

    def test_answer_after_finalized_is_false(self):
        """终局(已回填)后迟到/重复帧弃置:一笔回答只生效一次。"""
        async def scenario():
            bridge = WebApprovalBridge()
            fut = bridge.responder(REQ)
            bridge.answer(ApprovalDecision(
                approved=False, persist=False, source=DecisionSource.USER_ONCE))
            await fut
            return bridge.answer(ApprovalDecision(
                approved=True, persist=True, source=DecisionSource.USER_PERSIST))

        self.assertFalse(asyncio.run(scenario()),
                         "重复应答不得改写已终局的决定")

    def test_engine_timeout_cancels_slot_and_late_frame_dropped(self):
        """引擎超时掐死应答通道:挂起即时出槽,迟到帧弃置(fail-closed)。"""
        async def scenario():
            bridge = WebApprovalBridge()
            fut = bridge.responder(REQ)
            fut.cancel()  # 引擎 wait_for 超时即取消 future
            try:
                await fut
            except asyncio.CancelledError:
                pass
            return (bridge.pending, bridge.answer(ApprovalDecision(
                approved=True, persist=True, source=DecisionSource.USER_PERSIST)))

        pending, answered = asyncio.run(scenario())
        self.assertIsNone(pending, "死条目即时出槽,不滞留")
        self.assertFalse(answered, "超时后的批准帧不得复活已终局的审批")


# ============ 真实栈端到端:真门真 interrupt 经 WS 的审批闭环 ============

def _hazard_write_stub(calls: list) -> StructuredTool:
    """名为 write_office_file 的桩:分级按名入册(write 级),执行只记录不落盘。"""
    def run(filepath: str, content: str, mode: str = "w") -> str:
        calls.append(dict(filepath=filepath, content=content, mode=mode))
        return f"written:{filepath}"
    return StructuredTool.from_function(
        func=run, name="write_office_file", description="测试桩:写工具")


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:tests/test_approval_interrupt)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        return self.script.pop(0)


def _write_call(call_id: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "write_office_file", "args": args,
         "id": call_id, "type": "tool_call"}])


class ApprovalEndToEndBase(unittest.TestCase):
    """生产装配(assemble_backend_owner)+ 假 LLM/假工具,零真实网络。

    patch 须罩住整个 TestClient 生命周期(引擎在 lifespan 里构造);
    审计走真 logger 的旁路(会话级夹具锚定),REST 端点即取即查。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.calls: list = []

    def tearDown(self):
        self._tmp.cleanup()

    def _app(self, llm, check_interval=999):
        from entry.web_owner import assemble_backend_owner
        workspace = WorkspaceConfig.from_root(self._tmp.name)
        workspace.ensure_dirs()

        async def owner_factory():
            factory = assemble_backend_owner(
                thread_id=self.THREAD, provider_name="aliyun",
                model_name="glm-5", workspace=workspace,
                check_interval=check_interval)
            return await factory()

        return create_web_app(token=TOKEN, owner_factory=owner_factory), workspace

    def _patches(self, llm):
        return (
            patch("auditronclaw.core.agent.get_provider", return_value=llm),
            patch("auditronclaw.core.agent.build_builtin_tools",
                  return_value=[_hazard_write_stub(self.calls)]),
            patch("auditronclaw.core.agent.load_dynamic_skills",
                  return_value=[]),
        )

    def _audit_decisions(self, client) -> list[dict]:
        body = client.get("/api/audit", params={
            "token": TOKEN, "thread_id": self.THREAD,
            "event": "approval_decision"}).json()
        return body["entries"]


class TestApprovalFlowEndToEnd(ApprovalEndToEndBase):
    """完整闭环:WS 提交 → 审批帧 → decision 帧 → 同回合续行 → 审计留痕。"""

    THREAD = "web_approval_e2e"

    @staticmethod
    def _submit(ws, text: str, count: int) -> list[dict]:
        """提交回合并读 count 帧(审批帧读到即挂起已入桥,decision 帧必有物可答)。"""
        ws.send_json({"type": "input", "text": text})
        return [ws.receive_json() for _ in range(count)]

    @staticmethod
    def _receive(ws, count: int) -> list[dict]:
        return [ws.receive_json() for _ in range(count)]

    def test_approve_once_executes_exactly_the_approved_call(self):
        llm = ScriptedLLM([
            _write_call("call_1", {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已写完。"),
        ])
        app, _workspace = self._app(llm)
        with ExitStack() as stack:
            for p in self._patches(llm):
                stack.enter_context(p)
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                    head = self._submit(ws, "写一份日报", 2)
                    # 读到审批帧即挂起已入桥:回填一次批准,回合就地续行
                    ws.send_json({"type": "decision", "choice": "once"})
                    frames = head + self._receive(ws, 3)

        self.assertEqual([f["type"] for f in frames],
                         ["tool_call", "approval_request", "tool_result",
                          "reply", "turn_end"])
        request = frames[1]["payload"]
        self.assertEqual(request["tool"], "write_office_file")
        self.assertEqual(request["risk_class"], "write")
        self.assertEqual(request["args"]["filepath"], "reports/daily.md")
        self.assertEqual(request["timeout_seconds"], 300.0,
                         "默认超时随审批帧下发(卡面倒计时以此为限)")
        self.assertIn("written:reports/daily.md", frames[2]["payload"]["result"],
                      "批准后执行,结果作为 tool_result 返回")
        self.assertEqual(self.calls, [dict(filepath="reports/daily.md",
                                           content="x", mode="w")],
                         "执行的就是批准的那份参数(schema 规范化后)")
        decisions = self._audit_decisions(client)
        self.assertEqual([d["source"] for d in decisions], ["user_once"],
                         "批准一次留痕 source=user_once")
        self.assertIs(decisions[0]["approved"], True)

    def test_deny_returns_gate_rejection_and_turn_finishes(self):
        llm = ScriptedLLM([
            _write_call("call_1", {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已放弃。"),
        ])
        app, _workspace = self._app(llm)
        with ExitStack() as stack:
            for p in self._patches(llm):
                stack.enter_context(p)
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                    head = self._submit(ws, "写一份日报", 2)
                    ws.send_json({"type": "decision", "choice": "deny"})
                    frames = head + self._receive(ws, 3)

        self.assertIn(REJECT_PHRASE, frames[2]["payload"]["result"],
                      "拒绝话术作为 tool_result 返回 agent")
        self.assertIn("操作员已明确拒绝", frames[2]["payload"]["result"],
                      "拒绝叙述按来路说话:人拒不说无人值守")
        self.assertEqual(self.calls, [], "被拒的调用不得触达原工具")
        self.assertEqual(frames[-1]["type"], "turn_end", "回合照常收口")
        decisions = self._audit_decisions(client)
        self.assertEqual([d["source"] for d in decisions], ["user_once"])
        self.assertIs(decisions[0]["approved"], False)

    def test_always_mints_rule_then_same_call_silent_rule_auto(self):
        llm = ScriptedLLM([
            _write_call("call_1", {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="第一回合完成。"),
            _write_call("call_2", {"filepath": "reports/daily.md", "content": "y"}),
            AIMessage(content="第二回合完成。"),
        ])
        app, workspace = self._app(llm)
        with ExitStack() as stack:
            for p in self._patches(llm):
                stack.enter_context(p)
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                    first_head = self._submit(ws, "写日报", 2)
                    ws.send_json({"type": "decision", "choice": "always"})
                    first = first_head + self._receive(ws, 3)
                    second = self._submit(ws, "再写一次", 4)
                decisions = self._audit_decisions(client)
                persisted = client.get("/api/audit", params={
                    "token": TOKEN, "thread_id": self.THREAD,
                    "event": "rule_persisted"}).json()["entries"]

        # 第一回合问了人,永久允许;第二回合同调用静默放行(rule_auto)
        self.assertEqual(first[1]["type"], "approval_request")
        self.assertNotIn("approval_request", [f["type"] for f in second],
                         "永久允许后同调用再触发不问")
        self.assertEqual(len(self.calls), 2, "两回合都真实执行")
        self.assertIn("written:reports/daily.md", second[1]["payload"]["result"])
        self.assertEqual([d["source"] for d in decisions],
                         ["user_persist", "rule_auto"])
        self.assertEqual(len(persisted), 1, "规则写入恰好一条(rule_persisted)")
        self.assertEqual(persisted[0]["rule"]["scope"], "office/reports/daily.md")
        with open(workspace.approval_rules_file, encoding="utf-8") as f:
            rules = json.load(f)
        self.assertEqual(len(rules), 1, "规则落盘,重启进程后仍生效")

    def test_no_answer_times_out_denies_and_turn_closes(self):
        """不答即拒:引擎超时终局拒绝、留痕 TIMEOUT、回合收口。"""
        llm = ScriptedLLM([
            _write_call("call_1", {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="未获批准,已放弃。"),
        ])
        app, _workspace = self._app(llm)
        with ExitStack() as stack:
            for p in self._patches(llm):
                stack.enter_context(p)
            stack.enter_context(patch.dict(os.environ,
                                           {"AUDITRONCLAW_APPROVAL_TIMEOUT": "0.3"}))
            with TestClient(app) as client:
                with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                    head = self._submit(ws, "写一份日报", 2)
                    # 不答:坐等引擎超时(0.3s),帧自动续流
                    frames = head + self._receive(ws, 3)
                decisions = self._audit_decisions(client)

        self.assertEqual(self.calls, [], "超时=拒绝,原工具不得执行")
        self.assertEqual(frames[1]["payload"]["timeout_seconds"], 0.3,
                         "环境变量配的超时随审批帧下发,卡面倒计时同源")
        self.assertIn(REJECT_PHRASE, frames[2]["payload"]["result"])
        self.assertIn("审批等待超时", frames[2]["payload"]["result"],
                      "超时拒的话术交代来路")
        self.assertEqual(frames[-1]["type"], "turn_end", "回合收口,队列不堵")
        self.assertEqual([d["source"] for d in decisions], ["timeout"],
                         "拒绝留痕 source=timeout")


class TestHeartbeatNeverAsks(ApprovalEndToEndBase):
    """心跳回合构造上不问人(引擎保证,本票在真实装配上复验)。"""

    THREAD = "web_approval_heartbeat"

    def test_heartbeat_hazard_call_rejected_without_approval_frame(self):
        llm = ScriptedLLM([
            _write_call("call_1", {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="心跳回合已处理。"),
        ])
        app, workspace = self._app(llm, check_interval=0.05)
        past = (datetime.now() - timedelta(minutes=1)).strftime(
            "%Y-%m-%d %H:%M:%S")
        with open(workspace.tasks_file, "w", encoding="utf-8") as f:
            json.dump([{"id": "demo01", "target_time": past,
                        "description": "心跳演练", "repeat": None,
                        "repeat_count": None}], f, ensure_ascii=False)
        with ExitStack() as stack:
            for p in self._patches(llm):
                stack.enter_context(p)
            with TestClient(app) as client:
                events = self._poll_snapshot_until_turn_end(client)
                decisions = self._audit_decisions(client)

        self.assertNotIn("approval_request", [e["type"] for e in events],
                         "心跳回合不产生审批卡(呈现层复验的服务端锚点)")
        self.assertEqual(self.calls, [], "无人值守且无规则:直接拒")
        self.assertIn(REJECT_PHRASE, events[1]["payload"]["result"])
        self.assertEqual({e["origin"] for e in events}, {"heartbeat"})
        self.assertEqual([d["source"] for d in decisions], ["unattended"])

    def _poll_snapshot_until_turn_end(self, client, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = client.get("/api/snapshot", params={"token": TOKEN}).json()
            if any(e["type"] == "turn_end" for e in body["events"]):
                return body["events"]
            time.sleep(0.05)
        self.fail(f"等心跳回合超时({timeout}s):快照端点始终未见 turn_end")


if __name__ == "__main__":
    unittest.main()
