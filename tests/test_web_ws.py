"""Web 终端 WS 契约与接线(05 票):真流上屏的服务端锚点。

WS 层测试钉契约的四个面(定稿记录见 entry/web_ws 模块 docstring):
- 握手鉴权:token 校验复用 02 中间件(错 token 拒握手 1008 已在
  test_web_server 钉过);此处钉有效 token 的正常通道与无属主形态
  (脚手架形态 accept 后 close 1011,对照 REST 503 的"不静默"口径)
- 连接即补发:last_seq 之后的缓存事件先发、缺省 0 全量重放——断线
  补发与刷新后的画面重建共用同一数据面(事件缓存);补发段 seq 与
  实时段无缝接续
- 上行帧:input 入队成 human 回合(origin 逐帧标记)并实时下发;
  decision 帧回填挂起审批、同回合续行(07 票,审批 stub 引擎);
  坏帧回 protocol_error 错误码不断连接
- 多连接扇出:一端提交,所有在线连接同帧收到

引擎用 Stub(与 test_web_owner 同构):每回合固定产出
tool_call / tool_result / reply / turn_end 四事件,帧数可预知。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from auditronclaw.core.approval.gate import DecisionSource, TurnOrigin
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnTrajectory,
)
from entry.web import create_web_app
from entry.web_owner import BackendOwner

TOKEN = "probe-token-0123456789abcdef"

# Stub 回合的固定事件序(帧数即测试的读取次数)
TURN_EVENT_TYPES = ["tool_call", "tool_result", "reply", "turn_end"]


class StubEngine:
    """假引擎:记录回合来源,每回合固定产出四事件。"""

    def __init__(self):
        self.turns = []

    async def run_turn(self, text, origin=TurnOrigin.UNATTENDED):
        self.turns.append((text, origin))
        yield ToolCall(name="probe", args={"q": text})
        yield ToolResult(tool="probe", result="ok")
        yield Reply(content=f"echo:{text}", final=True)
        yield TurnEnd(trajectory=TurnTrajectory(
            tool_calls=[{"tool": "probe", "args": {"q": text}}],
            tool_results=[{"tool": "probe", "result": "ok"}],
            reply=f"echo:{text}"))


class ApprovalStubEngine:
    """假引擎复刻引擎审批契约:yield ApprovalRequest 后等应答通道,应答后续行同回合。

    应答通道由属主桥注入(真实装配同形:engine 构造拿 bridge.responder,
    测试侧在 owner_factory 里接线——引擎先于属主存在,桥要两边同一只)。
    """

    def __init__(self):
        self.responder = None
        self.decisions = []

    # 复刻引擎契约的可配超时(worker 落审批帧时读它附进 payload);
    # 非默认值证明 payload 传的是引擎真值,不是常量
    approval_timeout = 123.0

    async def run_turn(self, text, origin=TurnOrigin.UNATTENDED):
        request = ApprovalRequest(tool="danger", args={"target": text},
                                  risk_class="write", reason="写类副作用")
        yield ToolCall(name="danger", args={"target": text})
        yield request
        decision = await self.responder(request)
        self.decisions.append(decision)
        result = "executed" if decision.approved else "rejected"
        yield ToolResult(tool="danger", result=result)
        yield Reply(content=f"done:{text}", final=True)
        yield TurnEnd(trajectory=TurnTrajectory(
            tool_calls=[{"tool": "danger", "args": {"target": text}}],
            tool_results=[{"tool": "danger", "result": result}],
            reply=f"done:{text}"))


class WsTestBase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tasks_file = os.path.join(self._tmp.name, "tasks.json")
        self.engine = StubEngine()

    def tearDown(self):
        self._tmp.cleanup()

    def _client(self, cache_size: int = 1000) -> TestClient:
        engine, tasks_file = self.engine, self.tasks_file

        async def owner_factory():
            return BackendOwner(engine=engine, tasks_file=tasks_file,
                                check_interval=999, cache_size=cache_size)

        return TestClient(create_web_app(token=TOKEN, owner_factory=owner_factory))

    def _ws_url(self, last_seq: int | None = None) -> str:
        url = f"/ws?token={TOKEN}"
        if last_seq is not None:
            url += f"&last_seq={last_seq}"
        return url

    def _drive_turn(self, client: TestClient, text: str) -> list[dict]:
        """经 WS 提交一回合并收完四帧(收帧即缓存已落,可安全断开)。"""
        with client.websocket_connect(self._ws_url()) as ws:
            ws.send_json({"type": "input", "text": text})
            return [ws.receive_json() for _ in TURN_EVENT_TYPES]


# ============ 握手与补发:连接即重放,last_seq 取缺口 ============

class TestWsHandshakeAndReplay(WsTestBase):

    def test_valid_token_replays_all_events_with_envelope_shape(self):
        with self._client() as client:
            self._drive_turn(client, "第一回合")

            with client.websocket_connect(self._ws_url(last_seq=0)) as ws:
                frames = [ws.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual([f["type"] for f in frames], TURN_EVENT_TYPES,
                         "缺省 last_seq=0 全量重放,事件序与缓存一致")
        self.assertEqual([f["seq"] for f in frames], [1, 2, 3, 4])
        self.assertEqual({f["origin"] for f in frames}, {"human"},
                         "操作员回合的补发帧 origin 逐帧标记")
        self.assertEqual(set(frames[0]), {"seq", "type", "origin", "payload"},
                         "下行信封四字段:seq/type/origin/payload,无浏览器特有载荷")
        self.assertEqual(frames[0]["payload"]["name"], "probe")
        self.assertEqual(frames[2]["payload"]["content"], "echo:第一回合")

    def test_reconnect_with_last_seq_gets_gap_only(self):
        with self._client() as client:
            self._drive_turn(client, "旧回合")

            with client.websocket_connect(self._ws_url(last_seq=2)) as ws:
                frames = [ws.receive_json() for _ in range(2)]

        self.assertEqual([f["seq"] for f in frames], [3, 4],
                         "last_seq=2 只补发缺口(seq > 2),已见帧不重发")
        self.assertEqual([f["type"] for f in frames], ["reply", "turn_end"])

    def test_seq_continues_from_replay_into_live(self):
        """断线重连的画面:补发段与实时段 seq 无缝衔接。"""
        with self._client() as client:
            self._drive_turn(client, "断线前")

            with client.websocket_connect(self._ws_url(last_seq=2)) as ws:
                replay = [ws.receive_json() for _ in range(2)]
                ws.send_json({"type": "input", "text": "重连后"})
                live = [ws.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual([f["seq"] for f in replay + live],
                         [3, 4, 5, 6, 7, 8],
                         "补发 3-4 接实时 5-8,操作员视角无断点")

    def test_full_replay_after_ring_overflow_starts_beyond_seq_one(self):
        """缓冲溢出后全量重放起点大于 1:客户端锚点规则据此设计
        (首帧重设锚点续播,不得以"非 1 即重建"死循环)。"""
        with self._client(cache_size=3) as client:
            self._drive_turn(client, "第一回合")
            self._drive_turn(client, "第二回合")

            with client.websocket_connect(self._ws_url(last_seq=0)) as ws:
                frames = [ws.receive_json() for _ in range(3)]

        self.assertEqual([f["seq"] for f in frames], [6, 7, 8],
                         "全量重放只含缓冲幸存的尾部,起点是 6 不是 1")


# ============ 上行帧:input 驱动回合,decision/bad 帧回错误码 ============

class TestWsUpstream(WsTestBase):

    def test_input_frame_drives_human_turn_live(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                ws.send_json({"type": "input", "text": "你好"})
                frames = [ws.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual([t for t, _o in self.engine.turns], ["你好"])
        self.assertEqual(self.engine.turns[0][1], TurnOrigin.HUMAN,
                         "input 帧入队成 human 回合(可问人)")
        self.assertEqual([f["type"] for f in frames], TURN_EVENT_TYPES)
        self.assertEqual(frames[0]["seq"], 1, "空缓存首帧 seq 从 1 起")

    def test_decision_frame_unavailable_but_connection_survives(self):
        """无挂起审批时的 decision 帧:回错误码不断连接。"""
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                ws.send_json({"type": "decision", "choice": "deny"})
                error = ws.receive_json()
                # 同一连接继续可用
                ws.send_json({"type": "input", "text": "继续"})
                frames = [ws.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual(error["type"], "protocol_error")
        self.assertEqual(error["payload"]["code"], "decision_unavailable")
        self.assertEqual(error["seq"], 0, "错误帧不入缓存,seq=0 不与流事件冲突")
        self.assertEqual(error["origin"], "server")
        self.assertEqual([f["type"] for f in frames], TURN_EVENT_TYPES,
                         "错误帧之后连接存活,后续回合照常收发")

    def test_bad_frames_get_error_codes_and_connection_survives(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                ws.send_text("{not-json")
                ws.send_json({"type": "input"})
                ws.send_json({"type": "input", "text": "   "})
                ws.send_json({"type": "wat"})
                errors = [ws.receive_json()["payload"]["code"] for _ in range(4)]
                ws.send_json({"type": "input", "text": "好的"})
                frames = [ws.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual(errors,
                         ["bad_frame", "input_empty", "input_empty", "unknown_type"],
                         "非 JSON/缺文本/空白文本/未知 type 各回各的错误码")
        self.assertEqual([f["type"] for f in frames], TURN_EVENT_TYPES,
                         "坏帧不断连接,合法帧照常受理")


# ============ 上行帧:decision 回答挂起审批,同回合续行(07 票) ============

class ApprovalWsTestBase(WsTestBase):
    """审批 stub 引擎的通道形态:approval_request 帧下发 → decision 帧回填。"""

    def setUp(self):
        super().setUp()
        self.engine = ApprovalStubEngine()

    def _client(self, cache_size: int = 1000) -> TestClient:
        engine, tasks_file = self.engine, self.tasks_file

        async def owner_factory():
            owner = BackendOwner(engine=engine, tasks_file=tasks_file,
                                 check_interval=999, cache_size=cache_size)
            engine.responder = owner.approval.responder
            return owner

        return TestClient(create_web_app(token=TOKEN, owner_factory=owner_factory))

    def _drive_to_approval(self, client: TestClient, ws, text: str) -> dict:
        """提交回合并读到审批帧(此时挂起已入桥,decision 帧必有物可答)。"""
        ws.send_json({"type": "input", "text": text})
        ws.receive_json()  # tool_call 帧先落,审批帧后到
        return ws.receive_json()


class TestWsDecisionFlow(ApprovalWsTestBase):

    def test_decision_once_resumes_turn_in_place(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                request = self._drive_to_approval(client, ws, "改一下")
                ws.send_json({"type": "decision", "choice": "once"})
                rest = [ws.receive_json() for _ in range(3)]

        self.assertEqual(request["type"], "approval_request")
        self.assertEqual(request["payload"]["tool"], "danger")
        self.assertEqual(request["payload"]["risk_class"], "write")
        self.assertEqual(request["payload"]["timeout_seconds"], 123.0,
                         "倒计时限值随审批帧下发引擎真实超时,客户端不自设期限")
        self.assertEqual([f["type"] for f in rest],
                         ["tool_result", "reply", "turn_end"],
                         "应答回填应答通道,同回合续行至收尾")
        self.assertEqual(rest[0]["payload"]["result"], "executed")
        decision = self.engine.decisions[0]
        self.assertIs(decision.approved, True)
        self.assertIs(decision.persist, False)
        self.assertIs(decision.source, DecisionSource.USER_ONCE)

    def test_decision_always_maps_to_persist(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                self._drive_to_approval(client, ws, "再改")
                ws.send_json({"type": "decision", "choice": "always"})
                for _ in range(3):
                    ws.receive_json()  # 收完续行帧,回合收尾

        decision = self.engine.decisions[0]
        self.assertIs(decision.approved, True)
        self.assertIs(decision.persist, True)
        self.assertIs(decision.source, DecisionSource.USER_PERSIST)

    def test_decision_deny_maps_to_human_rejection(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                self._drive_to_approval(client, ws, "别改")
                ws.send_json({"type": "decision", "choice": "deny"})
                rest = [ws.receive_json() for _ in range(3)]

        self.assertEqual(rest[0]["payload"]["result"], "rejected",
                         "拒绝作为 tool_result 返回,回合照常收尾")
        decision = self.engine.decisions[0]
        self.assertIs(decision.approved, False)
        self.assertIs(decision.source, DecisionSource.USER_ONCE,
                      "deny 是人的明确拒绝,不是无人值守")

    def test_invalid_choice_gets_decision_invalid_and_connection_survives(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                self._drive_to_approval(client, ws, "无效选项")
                ws.send_json({"type": "decision", "choice": "yes"})
                error = ws.receive_json()
                # 无效帧不断连接:补一个合法应答,回合仍收尾
                ws.send_json({"type": "decision", "choice": "deny"})
                for _ in range(3):
                    ws.receive_json()

        self.assertEqual(error["type"], "protocol_error")
        self.assertEqual(error["payload"]["code"], "decision_invalid",
                         "choice 缺失/不认识按无效应答拒,不是悄悄默认")
        self.assertEqual(self.engine.decisions[0].approved, False)

    def test_stale_decision_after_resolution_is_unavailable(self):
        """已终局的审批再收 decision:无物可答,回 decision_unavailable。"""
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as ws:
                self._drive_to_approval(client, ws, "第一笔")
                ws.send_json({"type": "decision", "choice": "once"})
                for _ in range(3):
                    ws.receive_json()  # 收完续行帧,回合收尾
                ws.send_json({"type": "decision", "choice": "deny"})
                error = ws.receive_json()

        self.assertEqual(error["payload"]["code"], "decision_unavailable")
        self.assertEqual(len(self.engine.decisions), 1,
                         "迟到帧弃置,不得改写已终局的决定")


# ============ 扇出:多连接同帧 ============

class TestWsFanout(WsTestBase):

    def test_two_connections_receive_same_events(self):
        with self._client() as client:
            with client.websocket_connect(self._ws_url()) as first, \
                    client.websocket_connect(self._ws_url()) as second:
                first.send_json({"type": "input", "text": "广播"})
                first_frames = [first.receive_json() for _ in TURN_EVENT_TYPES]
                second_frames = [second.receive_json() for _ in TURN_EVENT_TYPES]

        self.assertEqual(first_frames, second_frames,
                         "任一连接提交的回合,全部在线连接同帧收到")


# ============ 脚手架形态:无属主 ============

class TestWsScaffoldMode(unittest.TestCase):

    def test_no_owner_closes_with_1011(self):
        client = TestClient(create_web_app(token=TOKEN))
        with self.assertRaises(WebSocketDisconnect) as raised:
            with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                ws.receive_json()
        self.assertEqual(raised.exception.code, 1011)


if __name__ == "__main__":
    unittest.main()
