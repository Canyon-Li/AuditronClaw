"""会话引擎等价性测试(会话引擎 01 票主锚点)。

同一假 LLM 脚本跑两侧:SessionEngine(新解析)与基准 _drive_agent(旧手写
解析),断言两件事:① 回合轨迹逐字段等价(tool_calls / tool_results /
reply)② 回合事件流形状钉死(类型序、final 标记、TurnEnd 搭载轨迹、
异常上抛时不发 TurnEnd)。

TDD 流程:本文件先在 session.py 未实现时运行(import 即红),实现后全部通过。
01 票时基准照旧手写解析,等价性由本文件证明;02 票起 _drive_agent 改走
引擎驱动,本文件继续钉死事件语义与字面期望值(适配器全量契约另由
tests/test_bench_adapter_characterization.py 把守)。
"""
import ast
import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.session import (
    SessionEngine,
    TurnEvent,
    ToolCall,
    ToolResult,
    Reply,
    TurnEnd,
    ApprovalRequest,
    TurnTrajectory,
)

import bench_pipeline


# ============ 假件:脚本化 LLM + 探针工具(零真实网络) ============

@tool
def fake_probe(query: str) -> str:
    """测试探针工具 A。"""
    return f"probe-ok:{query}"


@tool
def fake_check(target: str) -> str:
    """测试探针工具 B。"""
    return f"check-ok:{target}"


FAKE_TOOLS = [fake_probe, fake_check]


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage;脚本项为异常实例时上抛(模拟中途故障)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self  # 绑定后仍是自己,invoke 继续吃脚本

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


TRIGGER = "查一下测试探针"

# 三类消息各覆盖一条:content+tool_calls 并存 / 纯 tool_calls / 纯文本收尾
SCRIPT = [
    AIMessage(
        content="先探测一下。",
        tool_calls=[{"name": "fake_probe", "args": {"query": "dir"},
                     "id": "call_1", "type": "tool_call"}],
    ),
    AIMessage(
        content="",
        tool_calls=[{"name": "fake_check", "args": {"target": "report"},
                     "id": "call_2", "type": "tool_call"}],
    ),
    AIMessage(content="探测与核对都完成了。"),
]


def _enter_fake_tool_patches(stack, llm):
    """现有注入点三件套 + 审批门入册:假 LLM + 假工具表 + 空技能表 + 假工具入副作用册。

    两侧(引擎/基准)都走内置工具工厂注入点而非 tools= 直给——_drive_agent
    内部自建 app 只吃这条路径,两侧必须同构工具表,等价性才成立。
    假探针工具按"新工具入册即加映射"纪律注册为纯读——本文件测的是解析
    等价性,不是审批门(门的行为由 tests/test_approval_gate.py 把守)。
    """
    from auditronclaw.core.approval import classifier
    for p in (
        patch('auditronclaw.core.agent.get_provider', return_value=llm),
        patch('auditronclaw.core.agent.build_builtin_tools', return_value=FAKE_TOOLS),
        patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]),
        patch.object(classifier, "_PURE_READ_TOOLS",
                     classifier._PURE_READ_TOOLS | {"fake_probe", "fake_check"}),
    ):
        stack.enter_context(p)


def _build_app(llm, stack):
    """走现有注入点构造 agent app:假 LLM + 假工具表 + 空技能表 + 假工具入册。

    patch 由调用方的 stack 持有,须罩住整个运行期——审批门分级发生在工具
    调用时(而非 app 构造时),构造完就撤 patch 会让假工具在门处被判未入册。
    """
    from auditronclaw.core.agent import create_agent_app
    from auditronclaw.core.config import WorkspaceConfig
    _enter_fake_tool_patches(stack, llm)
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="session_engine_ws_"))
    workspace.ensure_dirs()
    return create_agent_app(
        provider_name="fake",
        model_name="fake-model",
        workspace=workspace,
        checkpointer=MemorySaver(),
        thread_id="session_engine_test",
    )


async def _drive_engine(app, thread_id, text):
    """跑引擎一个回合,收集全部回合事件。"""
    events = []
    async for ev in SessionEngine(app, thread_id).run_turn(text):
        events.append(ev)
    return events


class TestTrajectoryEquivalence(unittest.TestCase):
    """断言①:同一脚本下,引擎轨迹 ≡ 基准 _drive_agent 现收集结果(逐字段)。"""

    def test_trajectory_matches_baseline_drive_agent(self):
        # 侧一:引擎(TurnEnd 携带聚合轨迹)
        with ExitStack() as stack:
            app = _build_app(ScriptedLLM(SCRIPT), stack)
            engine_events = asyncio.run(_drive_engine(app, "equiv/engine", TRIGGER))
        self.assertIsInstance(engine_events[-1], TurnEnd)
        traj = engine_events[-1].trajectory

        # 侧二:基准现解析(_drive_agent 内部自建 app,patch 须罩住整个运行期)
        case = {"id": "engine", "surface": "equiv", "trigger": TRIGGER}
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            _enter_fake_tool_patches(stack, ScriptedLLM(SCRIPT))
            from auditronclaw.core.config import WorkspaceConfig
            workspace = WorkspaceConfig.from_root(tmp)
            workspace.ensure_dirs()
            raw = asyncio.run(bench_pipeline._drive_agent(
                case, workspace, "fake-model", "fake", "equiv", []))

        self.assertEqual(traj.tool_calls, raw["tool_calls"], "tool_calls 逐字段等价")
        self.assertEqual(traj.tool_results, raw["tool_results"], "tool_results 逐字段等价")
        self.assertEqual(traj.reply, raw["reply"], "reply 等价")

        # 锚死具体语义(不依赖两侧共同跑通即算过):
        # - 并存消息的文本进 reply(基准语义:并列 if,非 if/elif)
        # - 空 content 的 tool_call 消息不进 reply
        self.assertEqual(traj.tool_calls, [
            {"tool": "fake_probe", "args": {"query": "dir"}},
            {"tool": "fake_check", "args": {"target": "report"}},
        ])
        self.assertEqual(traj.tool_results, [
            {"tool": "fake_probe", "result": "probe-ok:dir"},
            {"tool": "fake_check", "result": "check-ok:report"},
        ])
        self.assertEqual(traj.reply, "先探测一下。\n探测与核对都完成了。")


class TestEventStreamShape(unittest.TestCase):
    """断言②:事件流形状钉死——类型序、final 标记、TurnEnd 搭载轨迹。"""

    def test_event_sequence_and_fields(self):
        with ExitStack() as stack:
            app = _build_app(ScriptedLLM(SCRIPT), stack)
            events = asyncio.run(_drive_engine(app, "shape/engine", TRIGGER))

        # 类型序:并存消息先发 ToolCall 再发 Reply(final=False)
        self.assertEqual(
            [type(e) for e in events],
            [ToolCall, Reply, ToolResult, ToolCall, ToolResult, Reply, TurnEnd],
        )
        self.assertEqual(events[0], ToolCall(name="fake_probe", args={"query": "dir"}))
        self.assertEqual(events[1], Reply(content="先探测一下。", final=False))
        # ToolResult 带完整结果文本,不截断
        self.assertEqual(events[2], ToolResult(tool="fake_probe", result="probe-ok:dir"))
        self.assertEqual(events[3], ToolCall(name="fake_check", args={"target": "report"}))
        self.assertEqual(events[4], ToolResult(tool="fake_check", result="check-ok:report"))
        # 纯文本收尾消息 final=True
        self.assertEqual(events[5], Reply(content="探测与核对都完成了。", final=True))

        # TurnEnd 搭载聚合好的回合轨迹,且是最后一个事件
        self.assertEqual(events[6].trajectory, TurnTrajectory(
            tool_calls=[
                {"tool": "fake_probe", "args": {"query": "dir"}},
                {"tool": "fake_check", "args": {"target": "report"}},
            ],
            tool_results=[
                {"tool": "fake_probe", "result": "probe-ok:dir"},
                {"tool": "fake_check", "result": "check-ok:report"},
            ],
            reply="先探测一下。\n探测与核对都完成了。",
        ))


class TestExceptionPropagates(unittest.TestCase):
    """回合中途异常:原样上抛、不发 TurnEnd。"""

    def test_mid_turn_exception_raises_without_turn_end(self):
        script = [
            AIMessage(
                content="",
                tool_calls=[{"name": "fake_probe", "args": {"query": "x"},
                             "id": "call_1", "type": "tool_call"}],
            ),
            RuntimeError("模拟回合中途故障"),
        ]
        with ExitStack() as stack:
            app = _build_app(ScriptedLLM(script), stack)

            async def run():
                events = []
                with self.assertRaises(RuntimeError) as ctx:
                    async for ev in SessionEngine(app, "boom/engine").run_turn(TRIGGER):
                        events.append(ev)
                return events, ctx

            events, ctx = asyncio.run(run())
        self.assertEqual(str(ctx.exception), "模拟回合中途故障", "异常原样上抛")
        self.assertFalse(
            [e for e in events if isinstance(e, TurnEnd)],
            "异常回合不得发 TurnEnd",
        )


class TestEventInterface(unittest.TestCase):
    """事件类型接口钉子:frozen dataclass + TurnEvent 子类(审批打断事件含字段)。"""

    def test_all_event_types_are_frozen_turn_events(self):
        samples = [
            ToolCall(name="t", args={}),
            ToolResult(tool="t", result="r"),
            Reply(content="c", final=True),
            ApprovalRequest(tool="t", args={}, risk_class="write", reason="r"),
            TurnEnd(trajectory=TurnTrajectory([], [], "")),
        ]
        for ev in samples:
            with self.subTest(type=type(ev).__name__):
                self.assertIsInstance(ev, TurnEvent)
                with self.assertRaises(FrozenInstanceError):
                    ev.payload = 1  # frozen:任何字段赋值都必须被拒


class TestModuleBoundary(unittest.TestCase):
    """引擎模块边界钉子:不吃队列、不管心跳、不做审计、不 import agent。"""

    def test_session_module_imports_no_consumers_or_infra(self):
        import auditronclaw.core.session as session_mod
        src = Path(session_mod.__file__).read_text(encoding="utf-8")
        imported = []
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        # agent(reload 链)、bus/heartbeat(队列与心跳)、logger(审计埋点)都不得进引擎
        forbidden = ("agent", "bus", "heartbeat", "logger")
        for mod in imported:
            for word in forbidden:
                self.assertNotIn(
                    word, mod,
                    f"引擎不得 import {word} 相关模块(违反模块边界): {mod}")


if __name__ == '__main__':
    unittest.main()
