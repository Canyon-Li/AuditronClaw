"""Web 终端重启历史重建(06 票):后端重启后浏览器重连不白屏。

数据面是事件缓存(快照端点与 WS 重放共用),重启后缓存空——历史从
checkpointer 消息存档做消息级粗重建播回缓存:

- 纯映射:存档消息 → 回合事件(tool_call / tool_result / reply 按消息序,
  HumanMessage 分段回合、段尾 turn_end 携轨迹聚合)——与实时流同一事件
  类型,信封形状经 serialize_turn_event 单点落成;审批过程事件
  (approval_request)不在存档里,自然不重建
- 属主播种:start() 时缓存空且引擎有存档读取面(archived_messages)才
  重建,事件 origin=history 与实时流可区分,seq 自 1、先于 worker 启动
  ——历史段与实时段次序天然分明;缓存命中(非空)即跳过(快路径,
  重建每进程至多一次)
- 重启端到端:生产装配(AsyncSqliteSaver 落盘)跑几回合 → 进程收口 →
  同一工作区重启第二个属主 → REST 快照与 WS 全量重放均见历史,
  新回合 seq 接续历史之后(浏览器刷新不白屏的服务端锚点)
"""
import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    SessionEngine,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnTrajectory,
)
from entry.web import create_web_app
from entry.web_owner import (
    HISTORY_ORIGIN,
    BackendOwner,
    history_events_from_messages,
)

TOKEN = "probe-token-0123456789abcdef"


# ============ 假件:脚本化 LLM + 探针工具(零真实网络) ============

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


def _call(call_id: str, query: str) -> dict:
    return {"name": "fake_probe", "args": {"query": query},
            "id": call_id, "type": "tool_call"}


def _enter_fake_tool_patches(stack, llm):
    """注入点三件套 + 假工具入副作用册(与 test_web_owner 同构)。"""
    from auditronclaw.core.approval import classifier
    for p in (
        patch('auditronclaw.core.agent.get_provider', return_value=llm),
        patch('auditronclaw.core.agent.build_builtin_tools', return_value=FAKE_TOOLS),
        patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]),
        patch.object(classifier, "_PURE_READ_TOOLS",
                     classifier._PURE_READ_TOOLS | {"fake_probe"}),
    ):
        stack.enter_context(p)


def _build_engine(*, checkpointer, thread_id="t_history"):
    """真实装配:SessionEngine 绑给定的 checkpointer(假件经注入点注入,
    调用方 ExitStack 罩住整个运行期)。"""
    from auditronclaw.core.agent import create_agent_app
    tmp = tempfile.mkdtemp()
    workspace = WorkspaceConfig.from_root(tmp)
    workspace.ensure_dirs()
    app = create_agent_app(provider_name="aliyun", model_name="glm-5",
                           workspace=workspace, checkpointer=checkpointer,
                           thread_id=thread_id)
    return SessionEngine(app, thread_id)


# ============ 纯映射:存档消息 → 回合事件 ============

class TestHistoryMapping(unittest.TestCase):
    """消息级形状钉死:与实时流同一事件类型与字段(信封形状单点同源)。"""

    TWO_TURN_MESSAGES = [
        HumanMessage(content="第一问"),
        AIMessage(content="先探测。",
                  tool_calls=[_call("call_1", "dir")]),
        ToolMessage(content="probe-ok:dir", name="fake_probe",
                    tool_call_id="call_1"),
        AIMessage(content="第一回合完成。"),
        HumanMessage(content="第二问"),
        AIMessage(content="",
                  tool_calls=[_call("call_2", "x")]),
        ToolMessage(content="probe-ok:x", name="fake_probe",
                    tool_call_id="call_2"),
        AIMessage(content="第二回合完成。"),
    ]

    def test_two_turns_map_to_live_event_sequence(self):
        self.assertEqual(history_events_from_messages(self.TWO_TURN_MESSAGES), [
            ToolCall(name="fake_probe", args={"query": "dir"}),
            Reply(content="先探测。", final=False),
            ToolResult(tool="fake_probe", result="probe-ok:dir"),
            Reply(content="第一回合完成。", final=True),
            TurnEnd(trajectory=TurnTrajectory(
                tool_calls=[{"tool": "fake_probe", "args": {"query": "dir"}}],
                tool_results=[{"tool": "fake_probe", "result": "probe-ok:dir"}],
                reply="先探测。\n第一回合完成。")),
            ToolCall(name="fake_probe", args={"query": "x"}),
            ToolResult(tool="fake_probe", result="probe-ok:x"),
            Reply(content="第二回合完成。", final=True),
            TurnEnd(trajectory=TurnTrajectory(
                tool_calls=[{"tool": "fake_probe", "args": {"query": "x"}}],
                tool_results=[{"tool": "fake_probe", "result": "probe-ok:x"}],
                reply="第二回合完成。")),
        ], "消息序重放:AI 消息先工具调用后回复(与实时流同序,"
        "content 与调用并存时 reply final=False),HumanMessage 分段、"
        "段尾 turn_end 携轨迹聚合")

    def test_no_approval_or_input_events_reconstructed(self):
        """不逐字复刻事件流:审批过程事件不重建,输入文本不单独成事件。"""
        events = history_events_from_messages(self.TWO_TURN_MESSAGES)
        self.assertEqual({type(e) for e in events},
                         {ToolCall, ToolResult, Reply, TurnEnd},
                         "重建只有四种消息级事件,不虚构别型")
        self.assertNotIn(ApprovalRequest, {type(e) for e in events})
        for human_text in ("第一问", "第二问"):
            self.assertNotIn(human_text, str(events),
                             "回合输入文本不进入任何事件(实时流本就没有这一型)")

    def test_trailing_unfinished_turn_closes_with_what_exists(self):
        """重启时正在跑的回合不会续跑:按存档现状收口,如实交代已有轨迹。"""
        messages = [
            HumanMessage(content="跑到一半"),
            AIMessage(content="", tool_calls=[_call("call_9", "mid")]),
            ToolMessage(content="probe-ok:mid", name="fake_probe",
                        tool_call_id="call_9"),
        ]
        self.assertEqual(
            [type(e).__name__ for e in history_events_from_messages(messages)],
            ["ToolCall", "ToolResult", "TurnEnd"])

    def test_bare_human_turn_produces_nothing(self):
        """无应答的回合(如引擎即刻异常)不产空 turn_end。"""
        messages = [HumanMessage(content="第一问"),
                    HumanMessage(content="重试"),
                    AIMessage(content="好了。")]
        self.assertEqual(
            [type(e).__name__ for e in history_events_from_messages(messages)],
            ["Reply", "TurnEnd"])

    def test_leading_ai_and_foreign_types(self):
        """防御:无 HumanMessage 开头的 AI 消息仍成一段;不该出现的类型跳过。"""
        from langchain_core.messages import SystemMessage
        messages = [SystemMessage(content="不该出现在存档"),
                    AIMessage(content="开头的回复"),
                    AIMessage(content="", tool_calls=[_call("call_5", "y")]),
                    ToolMessage(content="probe-ok:y", name="fake_probe",
                                tool_call_id="call_5")]
        self.assertEqual(
            [type(e).__name__ for e in history_events_from_messages(messages)],
            ["Reply", "ToolCall", "ToolResult", "TurnEnd"])

    def test_empty_history_maps_to_nothing(self):
        self.assertEqual(history_events_from_messages([]), [])


# ============ 属主播种:缓存空重建、缓存命中跳过 ============

class _BoobyEngine:
    """带存档读取面的假引擎:archived_messages 被调即红(重建不应发生)。"""

    async def archived_messages(self):
        raise AssertionError("快路径:缓存命中不得触发重建")


class TestOwnerSeedsHistory(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tasks_file = os.path.join(self._tmp.name, "tasks.json")

    def tearDown(self):
        self._tmp.cleanup()

    def test_start_seeds_history_then_live_turns_continue_seq(self):
        """重启形态:同 app 新属主,历史播入缓存(origin=history、seq 自 1),
        新回合事件 seq 接续历史之后、origin 回到回合来源。"""
        llm = ScriptedLLM([
            AIMessage(content="", tool_calls=[_call("call_1", "重启前")]),
            AIMessage(content="重启前的收尾。"),
            AIMessage(content="重启后的收尾。"),
        ])
        with ExitStack() as stack:
            _enter_fake_tool_patches(stack, llm)
            engine = _build_engine(checkpointer=MemorySaver())

            async def run_turn_before_restart():
                async for _event in engine.run_turn("重启前的回合"):
                    pass

            asyncio.run(run_turn_before_restart())

            owner = BackendOwner(engine=engine, tasks_file=self.tasks_file,
                                 check_interval=999)

            async def drive():
                await owner.start()
                await owner.submit("重启后的回合")
                await owner.queue.join()
                await owner.stop()

            asyncio.run(drive())

        snap = owner.cache.snapshot()
        history = [e for e in snap if e.origin == HISTORY_ORIGIN]
        live = [e for e in snap if e.origin != HISTORY_ORIGIN]
        self.assertEqual([e.seq for e in history], [1, 2, 3, 4],
                         "历史 4 事件自 seq 1 起(工具调用/结果/回复/收尾)")
        self.assertEqual([e.type for e in history],
                         ["tool_call", "tool_result", "reply", "turn_end"])
        self.assertEqual(history[2].payload["content"], "重启前的收尾。")
        self.assertEqual([e.seq for e in live], [5, 6],
                         "重启后的新回合接续历史之后,历史与实时次序不交叉")
        self.assertEqual({e.origin for e in live}, {"human"})

    def test_cache_hit_skips_reconstruction(self):
        """快路径:缓存命中(非空)不走重建,实时事件保持 seq 语义。"""
        owner = BackendOwner(engine=_BoobyEngine(),
                             tasks_file=self.tasks_file, check_interval=999)
        owner.cache.append("reply", "human", {"content": "已在缓存"})

        asyncio.run(_start_stop(owner))

        snap = owner.cache.snapshot()
        self.assertEqual(len(snap), 1, "缓存内容原样,未被重建事件顶位")

    def test_engine_without_archive_skips_reconstruction(self):
        """引擎无存档读取面(脚手架引擎)或存档为空(app 无 checkpointer):
        拿不到事实就空手而归,空历史续起、启动不炸。"""
        bare = SimpleNamespace()  # 连 archived_messages 读取面都没有
        no_cp = SessionEngine(SimpleNamespace(checkpointer=None), "t_no_cp")
        for engine in (bare, no_cp):
            with self.subTest(type(engine).__name__):
                owner = BackendOwner(engine=engine, tasks_file=self.tasks_file,
                                     check_interval=999)
                asyncio.run(_start_stop(owner))
                self.assertEqual(owner.cache.snapshot(), [],
                                 "无存档可读,缓存保持为空,启动不炸")


async def _start_stop(owner: BackendOwner) -> None:
    await owner.start()
    await owner.stop()


# ============ 重启端到端:生产装配,两个属主先后同一工作区 ============

class TestRestartEndToEnd(unittest.TestCase):
    """跑几回合 → 进程收口 → 同工作区重启 → 快照/WS 重放见历史。

    生产装配链(assemble_backend_owner + AsyncSqliteSaver 落盘):第一个
    TestClient 的生命周期即第一个属主进程,退出即"重启前收口";第二个
    TestClient 是重启后的属主,不经浏览器即覆盖 REST 与 WS 两条读取路径。
    """

    THREAD = "restart_e2e_thread"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = WorkspaceConfig.from_root(self._tmp.name)
        self.workspace.ensure_dirs()
        # 脚本跨两个属主进程消耗:重启前两回合 + 重启后一回合
        self.llm = ScriptedLLM([
            AIMessage(content="", tool_calls=[_call("call_1", "第一回合")]),
            AIMessage(content="第一回合收尾。"),
            AIMessage(content="第二回合收尾。"),
            AIMessage(content="重启后收尾。"),
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def _owner_factory(self):
        from entry.web_owner import assemble_backend_owner
        return assemble_backend_owner(
            thread_id=self.THREAD, provider_name="aliyun", model_name="glm-5",
            workspace=self.workspace, check_interval=999)

    def _app(self):
        return create_web_app(token=TOKEN, owner_factory=self._owner_factory())

    def test_restart_rebuilds_history_in_snapshot_and_ws_replay(self):
        with ExitStack() as stack:
            _enter_fake_tool_patches(stack, self.llm)

            # ---- 第一个属主进程:跑两个回合后收口 ----
            # 两回合走同一连接:连接即补发(last_seq 缺省 0 会先重放缓存),
            # 分连接读帧会把缓存重放误当新回合帧、又在回合在跑时撕连接
            with TestClient(self._app()) as client:
                with client.websocket_connect(f"/ws?token={TOKEN}") as ws:
                    ws.send_json({"type": "input", "text": "第一回合问题"})
                    first = [ws.receive_json() for _ in range(4)]
                    ws.send_json({"type": "input", "text": "第二回合问题"})
                    second = [ws.receive_json() for _ in range(2)]
                self.assertEqual([f["seq"] for f in first], [1, 2, 3, 4])
                self.assertEqual([f["seq"] for f in second], [5, 6])

            # ---- 重启:同工作区第二个属主进程 ----
            with TestClient(self._app()) as client:
                body = client.get("/api/snapshot",
                                  params={"token": TOKEN}).json()
                self.assertEqual(
                    [e["type"] for e in body["events"]],
                    ["tool_call", "tool_result", "reply", "turn_end",
                     "reply", "turn_end"],
                    "快照端点见两回合的消息级历史(第二回合纯回复)")
                self.assertEqual({e["origin"] for e in body["events"]},
                                 {HISTORY_ORIGIN},
                                 "重建事件 origin=history,与实时流可区分")
                self.assertEqual([e["seq"] for e in body["events"]],
                                 [1, 2, 3, 4, 5, 6],
                                 "重建事件自 seq 1 重新编号")
                self.assertEqual(body["events"][2]["payload"]["content"],
                                 "第一回合收尾。")
                self.assertNotIn("approval_request",
                                 [e["type"] for e in body["events"]])
                self.assertEqual(body["latest_seq"], 6)

                # 浏览器刷新路径:WS 全量重放(last_seq=0)见历史
                with client.websocket_connect(
                        f"/ws?token={TOKEN}&last_seq=0") as ws:
                    replay = [ws.receive_json() for _ in range(6)]
                    self.assertEqual([f["seq"] for f in replay],
                                     [1, 2, 3, 4, 5, 6])
                    self.assertEqual({f["origin"] for f in replay},
                                     {HISTORY_ORIGIN})

                    # 重启后的新回合:seq 接续历史之后,origin 回到回合来源
                    ws.send_json({"type": "input", "text": "重启后的问题"})
                    live = [ws.receive_json() for _ in range(2)]
                    self.assertEqual([f["seq"] for f in live], [7, 8])
                    self.assertEqual(live[0]["origin"], "human")
                    self.assertEqual(live[0]["payload"]["content"],
                                     "重启后收尾。")

                # 快照随后反映历史 + 新回合的合流
                after = client.get("/api/snapshot",
                                   params={"token": TOKEN}).json()
                self.assertEqual([e["origin"] for e in after["events"]],
                                 [HISTORY_ORIGIN] * 6 + ["human", "human"])


if __name__ == "__main__":
    unittest.main()
