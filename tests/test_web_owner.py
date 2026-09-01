"""Web 终端后端属主(04 票):引擎/队列/心跳/事件缓存/审计旁路。

属主进程内装配的部件各有锚点测试:
- 审计订阅者:log_event 的只读旁路——纯增量回调,订阅者存在与否
  不改变 JSONL 写路径(既有语义全部保持通过)
- 事件缓存:有界环形缓冲、seq 单调、since 过滤(快照端点与后续
  重连补发的数据面)
- 属主 worker:操作员回合与心跳系统消息同队、单 worker 串行,
  origin 类型化标记进缓存,回合异常不杀 worker
- REST 端点:/api/snapshot 与 /api/audit(token 过门后 curl 即可见)

端到端:真实引擎装配(假 LLM/假工具,零真实网络)+ 到期心跳任务 →
快照端点见 tool_call / tool_result / reply / turn_end 序列(origin 全为
heartbeat),审计端点见同笔留痕——不开浏览器的完整回合演示。
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
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from auditronclaw.core.approval.gate import TurnOrigin
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.logger import JSONLEventLogger, get_audit_logger
from auditronclaw.core.session import (
    Reply,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnTrajectory,
)
from entry.web import create_web_app
from entry.web_owner import (
    AuditTap,
    BackendOwner,
    EventCache,
    serialize_turn_event,
)

TOKEN = "probe-token-0123456789abcdef"


# ============ 审计订阅者:只读旁路,写路径零改动 ============

class TestAuditSubscriber(unittest.TestCase):
    """订阅机制钉三点:纯增量递送、只读(拿到的是拷贝)、故障不外溢。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.logger = JSONLEventLogger(log_dir=os.path.join(self._tmp.name, "logs"))

    def tearDown(self):
        # 不 shutdown 写线程(atexit 收尾,先例:test_audit_hardening);
        # 先排空写队列再删临时目录——Windows 下写线程持有句柄时删除即失败
        self.logger.log_queue.join()
        self._tmp.cleanup()

    def _system_lines(self):
        path = os.path.join(self.logger.log_dir, "system.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_subscriber_receives_entries_incrementally(self):
        seen = []
        unsubscribe = self.logger.add_subscriber(seen.append)

        self.logger.log_event("t1", "probe_event", content="first")
        self.logger.log_event("t1", "probe_event", content="second")
        unsubscribe()

        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0]["thread_id"], "t1")
        self.assertEqual(seen[0]["event"], "probe_event")
        self.assertEqual(seen[0]["content"], "first")
        self.assertEqual(seen[1]["content"], "second")
        self.assertIn("ts", seen[0], "审计条目自带时间戳,订阅者拿到完整条目")

    def test_jsonl_write_path_unchanged_with_subscriber(self):
        """有订阅者时落盘与无订阅者同形:旁路纯增量,不替代不拦截。"""
        self.logger.add_subscriber(lambda entry: None)
        self.logger.log_event("system", "probe_event", marker="m-1")
        self.logger.log_queue.join()

        lines = self._system_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["marker"], "m-1")

    def test_subscriber_exception_does_not_break_logging(self):
        def bad_subscriber(_entry):
            raise RuntimeError("subscriber boom")

        unsubscribe = self.logger.add_subscriber(bad_subscriber)
        try:
            self.logger.log_event("system", "probe_event", marker="m-2")
        finally:
            unsubscribe()
        self.logger.log_queue.join()

        lines = self._system_lines()
        self.assertEqual(len(lines), 1, "订阅者故障不得丢事件——写路径先落队列")

    def test_subscriber_gets_copy_and_cannot_tamper_queue_item(self):
        self.logger.add_subscriber(lambda entry: entry.update(event="tampered"))
        self.logger.log_event("system", "probe_event", marker="m-3")
        self.logger.log_queue.join()

        lines = self._system_lines()
        self.assertEqual(lines[0]["event"], "probe_event",
                         "订阅者改写只影响自己的拷贝,落盘条目原样")

    def test_unsubscribe_stops_notifications(self):
        seen = []
        unsubscribe = self.logger.add_subscriber(seen.append)
        self.logger.log_event("system", "probe_event", marker="before")
        unsubscribe()
        self.logger.log_event("system", "probe_event", marker="after")

        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["marker"], "before")

    def test_multiple_subscribers_all_notified(self):
        first, second = [], []
        unsub1 = self.logger.add_subscriber(first.append)
        unsub2 = self.logger.add_subscriber(second.append)
        try:
            self.logger.log_event("system", "probe_event", marker="m-4")
        finally:
            unsub1()
            unsub2()

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)


# ============ 回合事件序列化:五种事件 → (type, payload) ============

class TestSerializeTurnEvent(unittest.TestCase):

    def test_five_event_types_map_to_envelope_fields(self):
        cases = [
            (ToolCall(name="probe", args={"q": "x"}),
             "tool_call", {"name": "probe", "args": {"q": "x"}}),
            (ToolResult(tool="probe", result="ok"),
             "tool_result", {"tool": "probe", "result": "ok"}),
            (Reply(content="你好", final=True),
             "reply", {"content": "你好", "final": True}),
            (Reply(content="先看一下", final=False),
             "reply", {"content": "先看一下", "final": False}),
            (TurnEnd(trajectory=TurnTrajectory(
                tool_calls=[{"tool": "probe", "args": {}}],
                tool_results=[{"tool": "probe", "result": "ok"}],
                reply="完成")),
             "turn_end", {"tool_calls": [{"tool": "probe", "args": {}}],
                          "tool_results": [{"tool": "probe", "result": "ok"}],
                          "reply": "完成"}),
        ]
        from auditronclaw.core.session import ApprovalRequest
        approval = ApprovalRequest(tool="run_script", args={"path": "a.py"},
                                   risk_class="interpreter", reason="解释器执行")
        cases.append((approval, "approval_request",
                      {"tool": "run_script", "args": {"path": "a.py"},
                       "risk_class": "interpreter", "reason": "解释器执行"}))

        for event, expected_type, expected_payload in cases:
            with self.subTest(expected_type):
                type_name, payload = serialize_turn_event(event)
                self.assertEqual(type_name, expected_type)
                self.assertEqual(payload, expected_payload)


# ============ 事件缓存:有界环形缓冲,seq 单调 ============

class TestEventCache(unittest.TestCase):

    def test_seq_monotonic_and_snapshot_since_filter(self):
        cache = EventCache(maxlen=8)
        for i in range(4):
            cache.append("reply", "human", {"n": i})

        snap = cache.snapshot()
        self.assertEqual([e.seq for e in snap], [1, 2, 3, 4], "seq 从 1 起单调递增")
        self.assertEqual([e.payload["n"] for e in snap], [0, 1, 2, 3])
        self.assertEqual(cache.latest_seq, 4)

        since = cache.snapshot(since=2)
        self.assertEqual([e.seq for e in since], [3, 4], "since 过滤返回 seq 之后的增量")

    def test_bounded_ring_drops_oldest_but_seq_keeps_climbing(self):
        cache = EventCache(maxlen=3)
        for i in range(5):
            cache.append("reply", "heartbeat", {"n": i})

        snap = cache.snapshot()
        self.assertEqual([e.seq for e in snap], [3, 4, 5], "环形缓冲丢最旧,seq 不回卷")
        self.assertEqual(cache.latest_seq, 5)
        self.assertEqual(cache.snapshot(since=4)[0].seq, 5)

    def test_envelope_carries_origin(self):
        cache = EventCache()
        cache.append("reply", "human", {})
        cache.append("reply", "heartbeat", {})
        self.assertEqual([e.origin for e in cache.snapshot()], ["human", "heartbeat"])


# ============ 审计旁路缓冲:条目可查询 ============

class TestAuditTap(unittest.TestCase):

    def _tap_three(self):
        tap = AuditTap()
        tap({"ts": "t0", "thread_id": "thread-a", "event": "tool_call",
             "tool": "probe", "args": {}})
        tap({"ts": "t1", "thread_id": "thread-a", "event": "tool_result",
             "tool": "probe", "result_summary": "ok"})
        tap({"ts": "t2", "thread_id": "system", "event": "system_action",
             "content": "上下文压缩"})
        return tap

    def test_query_by_thread_id(self):
        tap = self._tap_three()
        rows = tap.query(thread_id="thread-a")
        self.assertEqual([r["event"] for r in rows], ["tool_call", "tool_result"])

    def test_query_by_event_and_since_and_limit(self):
        tap = self._tap_three()
        self.assertEqual(len(tap.query(event="tool_call")), 1)
        self.assertEqual([r["seq"] for r in tap.query(since=1)], [2, 3])
        self.assertEqual([r["seq"] for r in tap.query(limit=2)], [2, 3],
                         "limit 取过滤后的最新 N 条,不吞尾")
        self.assertEqual(tap.query(limit=0), [], "limit=0 即空结果,不视为缺省")
        self.assertEqual(tap.query(limit=-1), [], "负 limit 拒绝取全量,按空处理")

    def test_seq_monotonic_and_bounded(self):
        tap = AuditTap(maxlen=2)
        for i in range(4):
            tap({"thread_id": "t", "event": "e", "i": i})
        rows = tap.query()
        self.assertEqual([r["seq"] for r in rows], [3, 4], "旁路缓冲有界,seq 单调")


# ============ 属主 worker:操作员与心跳同队串行 ============

class StubEngine:
    """假引擎:记录回合、强制暴露串行违背、可选抛异常。

    _active 断言是串行探针——单 worker 下两个回合绝不重叠;若重叠,
    断言失败以异常形态出现在该回合,测试据此变红。
    """

    def __init__(self):
        self.turns = []
        self._active = False
        self.fail_on = frozenset()

    async def run_turn(self, text, origin=TurnOrigin.UNATTENDED):
        assert not self._active, "回合重叠:单 worker 下上一回合未收尾不得开新回合"
        self._active = True
        try:
            self.turns.append((text, origin))
            await asyncio.sleep(0.02)
            if text in self.fail_on:
                raise RuntimeError(f"引擎异常: {text}")
            yield ToolCall(name="probe", args={"q": text})
            yield ToolResult(tool="probe", result="ok")
            yield Reply(content=f"echo:{text}", final=True)
            yield TurnEnd(trajectory=TurnTrajectory(
                tool_calls=[{"tool": "probe", "args": {"q": text}}],
                tool_results=[{"tool": "probe", "result": "ok"}],
                reply=f"echo:{text}"))
        finally:
            self._active = False


class BackendOwnerTestBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tasks_file = os.path.join(self._tmp.name, "tasks.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _owner(self, engine, check_interval=0.05):
        return BackendOwner(engine=engine, tasks_file=self.tasks_file,
                            check_interval=check_interval)

    def _write_due_task(self, description="到期演示任务"):
        past = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump([{
                "id": "demo01", "target_time": past,
                "description": description, "repeat": None, "repeat_count": None,
            }], f, ensure_ascii=False)

    @staticmethod
    def _wait_turn_ends(owner, count, timeout=5.0):
        """等属主跑完 count 个回合(缓存出现同数 turn_end),超时即红。"""

        async def wait():
            deadline = time.monotonic() + timeout
            while True:
                ends = [e for e in owner.cache.snapshot() if e.type == "turn_end"]
                if len(ends) >= count:
                    return
                if time.monotonic() > deadline:
                    raise TimeoutError("等 turn_end 超时")
                await asyncio.sleep(0.02)

        asyncio.run(wait())


class TestBackendOwnerQueue(BackendOwnerTestBase):

    def test_operator_inputs_queued_and_serial(self):
        engine = StubEngine()
        owner = self._owner(engine)

        async def drive():
            await owner.start()
            await owner.submit("第一条")
            await owner.submit("第二条")
            await owner.queue.join()
            await owner.stop()

        asyncio.run(drive())

        self.assertEqual([text for text, _o in engine.turns], ["第一条", "第二条"],
                         "操作员输入按提交序逐个消费")
        types = [e.type for e in owner.cache.snapshot()]
        self.assertEqual(types.count("turn_end"), 2)
        self.assertNotIn("turn_error", types, "串行违背或引擎异常都会在此现形")

    def test_origin_marked_in_cache(self):
        engine = StubEngine()
        owner = self._owner(engine)

        async def drive():
            await owner.start()
            await owner.submit("人来的")
            await owner.queue.join()
            await owner.stop()

        asyncio.run(drive())
        origins = {e.origin for e in owner.cache.snapshot()}
        self.assertEqual(origins, {"human"}, "操作员回合的缓存事件 origin 标记 human")

    def test_engine_exception_recorded_and_worker_survives(self):
        engine = StubEngine()
        engine.fail_on = frozenset({"会炸的"})
        owner = self._owner(engine)

        async def drive():
            await owner.start()
            await owner.submit("会炸的")
            await owner.submit("正常")
            await owner.queue.join()
            await owner.stop()

        asyncio.run(drive())

        snap = owner.cache.snapshot()
        errors = [e for e in snap if e.type == "turn_error"]
        self.assertEqual(len(errors), 1, "回合异常落缓存为 turn_error 事件")
        self.assertIn("引擎异常", errors[0].payload["error"])
        self.assertEqual([t for t, _o in engine.turns], ["会炸的", "正常"],
                         "异常回合不杀 worker,后续回合照常消费")

    def test_heartbeat_shares_queue_and_marks_origin(self):
        """心跳系统消息与操作员回合同队:pacemaker → 同一队列 → 同一 worker。"""
        engine = StubEngine()
        owner = self._owner(engine, check_interval=0.05)
        self._write_due_task(description="到期心跳演练")

        async def drive():
            await owner.start()
            await owner.submit("操作员先来")
            await owner.queue.join()
            # 心跳消息由 pacemaker 异步入队,join 后再等它跑完
            deadline = time.monotonic() + 5.0
            while len([t for t, o in engine.turns if o == TurnOrigin.HEARTBEAT]) < 1:
                if time.monotonic() > deadline:
                    raise TimeoutError("等心跳回合超时")
                await asyncio.sleep(0.02)
            await owner.queue.join()
            await owner.stop()

        asyncio.run(drive())

        origins = [(t, o) for t, o in engine.turns]
        self.assertEqual(origins[0][1], TurnOrigin.HUMAN, "先提交的操作员回合先跑")
        heartbeat = [t for t, o in origins if o == TurnOrigin.HEARTBEAT]
        self.assertEqual(len(heartbeat), 1, "到期任务恰好触发一条心跳回合")
        self.assertIn("系统内部心跳触发", heartbeat[0])
        cache_origins = {e.origin for e in owner.cache.snapshot()}
        self.assertEqual(cache_origins, {"human", "heartbeat"},
                         "两类回合共用同一缓存,origin 各自标记")

    def test_stop_is_idempotent_and_detaches_tap(self):
        engine = StubEngine()
        owner = self._owner(engine)

        async def drive():
            await owner.start()
            await owner.stop()
            await owner.stop()  # 二次 stop 不抛

        asyncio.run(drive())
        # 旁路已摘除:后续审计事件不再进属主缓冲
        before = len(owner.audit_tap.query())
        get_audit_logger().log_event("t1", "probe_event", marker="after-stop")
        self.assertEqual(len(owner.audit_tap.query()), before)


# ============ REST 端点:快照与审计查询(token 过门) ============

class TestWebAppWithOwner(BackendOwnerTestBase):

    def _app(self, engine):
        async def owner_factory():
            return self._owner(engine)

        return create_web_app(token=TOKEN, owner_factory=owner_factory)

    def test_snapshot_endpoint_shape_when_empty(self):
        with TestClient(self._app(StubEngine())) as client:
            response = client.get("/api/snapshot", params={"token": TOKEN})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body, {"events": [], "latest_seq": 0})

    def test_audit_endpoint_returns_tap_entries(self):
        app = self._app(StubEngine())
        with TestClient(app) as client:
            get_audit_logger().log_event("thread-a", "tool_call",
                                         tool="probe", args={"q": 1})
            response = client.get("/api/audit",
                                  params={"token": TOKEN, "thread_id": "thread-a"})
            self.assertEqual(response.status_code, 200)
            entries = response.json()["entries"]
            hit = [e for e in entries if e["event"] == "tool_call"
                   and e.get("tool") == "probe"]
            self.assertEqual(len(hit), 1, "审计端点可查到旁路捕获的同笔条目")

    def test_audit_endpoint_respects_limit(self):
        app = self._app(StubEngine())
        with TestClient(app) as client:
            for i in range(5):
                get_audit_logger().log_event("thread-b", "probe_event", i=i)
            response = client.get("/api/audit",
                                  params={"token": TOKEN, "thread_id": "thread-b",
                                          "limit": 2})
            self.assertEqual(len(response.json()["entries"]), 2)

    def test_endpoints_rejected_without_token(self):
        with TestClient(self._app(StubEngine())) as client:
            self.assertEqual(client.get("/api/snapshot").status_code, 403)
            self.assertEqual(client.get("/api/audit").status_code, 403)


class TestWebAppWithoutOwner(unittest.TestCase):
    """脚手架形态(无属主):端点存在但明确 503,不静默空数据。"""

    def test_snapshot_and_audit_return_503(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_web_app(token=TOKEN, static_dir=Path(tmp) / "absent")
            with TestClient(app) as client:
                for path in ("/api/snapshot", "/api/audit"):
                    response = client.get(path, params={"token": TOKEN})
                    self.assertEqual(response.status_code, 503, path)


# ============ 端到端:心跳驱动的完整回合落进缓存与审计 ============

@tool
def fake_probe(query: str) -> str:
    """测试探针工具。"""
    return f"probe-ok:{query}"


FAKE_TOOLS = [fake_probe]


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先调工具,再收尾回复)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        return self.script.pop(0)


class TestHeartbeatTurnEndToEnd(BackendOwnerTestBase):
    """真实引擎装配 + 到期心跳 → 快照/审计端点各见完整回合。

    不开浏览器:TestClient 即 curl 形态。假 LLM/假工具/空技能表走装配
    注入点(与 test_session_engine 同构),零真实网络。
    """

    THREAD = "e2e_heartbeat_thread"

    def _owner_factory(self, workspace):
        from entry.web_owner import assemble_backend_owner
        return assemble_backend_owner(
            thread_id=self.THREAD, provider_name="aliyun", model_name="glm-5",
            workspace=workspace, check_interval=0.05)

    def test_heartbeat_turn_visible_in_snapshot_and_audit(self):
        from auditronclaw.core.approval import classifier

        workspace = WorkspaceConfig.from_root(self._tmp.name)
        workspace.ensure_dirs()
        llm = ScriptedLLM([
            AIMessage(content="", tool_calls=[
                {"name": "fake_probe", "args": {"query": "heartbeat"},
                 "id": "call_1", "type": "tool_call"}]),
            AIMessage(content="心跳回合已完成探测。"),
        ])
        app = create_web_app(token=TOKEN,
                             owner_factory=self._owner_factory(workspace))

        with ExitStack() as stack:
            for p in (
                patch("auditronclaw.core.agent.get_provider", return_value=llm),
                patch("auditronclaw.core.agent.build_builtin_tools",
                      return_value=FAKE_TOOLS),
                patch("auditronclaw.core.agent.load_dynamic_skills",
                      return_value=[]),
                patch.object(classifier, "_PURE_READ_TOOLS",
                             classifier._PURE_READ_TOOLS | {"fake_probe"}),
            ):
                stack.enter_context(p)

            with TestClient(app) as client:
                # 心跳到期:任务在属主启动后落盘,pacemaker 下一拍触发
                self._write_due_task(description="跑一次探测演示")
                body = self._poll_until_turn_end(client)

                # 快照:一个完整回合,事件序与 origin 逐项钉死
                events = body["events"]
                self.assertEqual(
                    [e["type"] for e in events],
                    ["tool_call", "tool_result", "reply", "turn_end"],
                    "快照端点见 tool_call / tool_result / reply / turn_end 序列")
                self.assertEqual({e["origin"] for e in events}, {"heartbeat"},
                                 "心跳驱动回合的缓存事件 origin 全标记 heartbeat")
                self.assertEqual(events[0]["seq"], 1, "首回合事件 seq 从 1 起")
                self.assertEqual(events[-1]["seq"], body["latest_seq"])
                self.assertEqual(events[0]["payload"]["name"], "fake_probe")
                self.assertEqual(events[3]["payload"]["reply"], "心跳回合已完成探测。")

                # 审计:同笔留痕可见(工具调用与结果的审计条目)
                audit = client.get("/api/audit",
                                   params={"token": TOKEN,
                                           "thread_id": self.THREAD}).json()
                audit_events = [e["event"] for e in audit["entries"]]
                self.assertIn("tool_call", audit_events,
                              "工具调用落审计,旁路可查")
                self.assertIn("tool_result", audit_events,
                              "工具结果落审计,旁路可查")
                tool_call_entry = next(e for e in audit["entries"]
                                       if e["event"] == "tool_call")
                self.assertEqual(tool_call_entry["tool"], "fake_probe",
                                 "审计端点条目与快照里的调用是同一笔")

    def _poll_until_turn_end(self, client, timeout=8.0):
        """轮询快照端点直至心跳回合收尾(turn_end 出现),超时即红。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            body = client.get("/api/snapshot", params={"token": TOKEN}).json()
            if any(e["type"] == "turn_end" for e in body["events"]):
                return body
            time.sleep(0.05)
        self.fail(f"等心跳回合超时({timeout}s):快照端点始终未见 turn_end")


if __name__ == "__main__":
    unittest.main()
