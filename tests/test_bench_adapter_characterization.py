"""基准适配器表征测试(会话引擎 02 票重构护栏)。

先钉后切:在 _drive_agent 切换为引擎驱动之前,用假 LLM 缝(同 01 票
tests/test_session_engine.py 的三重 patch)把结果 dict 逐字段拍死——
切换只许换驱动方式,不许动输出形状与语义。这是重构护栏,不是先红后绿的
新行为:本文件对旧手写解析与引擎驱动实现都必须绿。

钉的对象是两个 judge(run_injection_bench / run_golden_eval)消费的完整
契约:case_id / surface / workspace / tool_calls / tool_results /
reply / pushes——键集、逐字段值、异常上抛、空轨迹形状。
"""
import asyncio
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

import bench_pipeline


# ============ 假件:脚本化 LLM + 探针工具(零真实网络,同 01 票缝) ============

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
    """现有缝三件套 + 审批门入册:假 LLM + 假工具表 + 空技能表 + 假工具入副作用册。

    假探针工具按"新工具入册即加映射"纪律注册为纯读——本文件钉的是适配器
    结果 dict 的形状,不是审批门(门的行为由 tests/test_approval_gate.py 把守)。
    """
    from auditronclaw.core.approval import classifier
    for p in (
        patch('auditronclaw.core.agent.get_provider', return_value=llm),
        patch('auditronclaw.core.agent.BUILTIN_TOOLS', FAKE_TOOLS),
        patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]),
        patch.object(classifier, "_PURE_READ_TOOLS",
                     classifier._PURE_READ_TOOLS | {"fake_probe", "fake_check"}),
    ):
        stack.enter_context(p)


def _drive(case, pushes, script=SCRIPT):
    """跑一次 _drive_agent,返回 (结果 dict, workspace 路径)。"""
    with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
        _enter_fake_tool_patches(stack, ScriptedLLM(script))
        raw = asyncio.run(bench_pipeline._drive_agent(
            case, tmp, "fake-model", "fake", "pin", pushes))
    return raw, tmp


class TestDriveAgentResultDict(unittest.TestCase):
    """结果 dict 全量钉子:键集、逐字段值、pushes 按引用搭载。"""

    def test_full_result_dict_pinned(self):
        pushes = []
        case = {"id": "pin_case", "surface": "pin_surface", "trigger": TRIGGER}
        raw, tmp = _drive(case, pushes)

        self.assertEqual(raw, {
            # 用例元数据原样搭载(judges 按键取用)
            "case_id": "pin_case",
            "surface": "pin_surface",
            "workspace": tmp,
            # 并存消息(先探测一下。+tool_calls)的文本进 reply(基准语义:
            # 并列收集,非 if/elif);空 content 的 tool_call 消息不进 reply
            "tool_calls": [
                {"tool": "fake_probe", "args": {"query": "dir"}},
                {"tool": "fake_check", "args": {"target": "report"}},
            ],
            "tool_results": [
                {"tool": "fake_probe", "result": "probe-ok:dir"},
                {"tool": "fake_check", "result": "check-ok:report"},
            ],
            "reply": "先探测一下。\n探测与核对都完成了。",
            "pushes": pushes,
        })

    def test_pushes_carried_by_reference(self):
        """pushes 捕获逻辑不动:mailbox fixture 的捕获列表被原样搭载(按引用,
        非复制)——run_case 传进来的列表就是 judge 读到的列表"""
        pushes = []
        case = {"id": "pin_case", "surface": "pin_surface", "trigger": TRIGGER}
        raw, _ = _drive(case, pushes)
        self.assertIs(raw["pushes"], pushes)


class TestDriveAgentEdgeShapes(unittest.TestCase):
    """边界形状钉子:空轨迹是空列表(非 None),异常原样上抛(runner 按用例捕获)。"""

    def test_pure_reply_turn_yields_empty_lists(self):
        case = {"id": "pure", "surface": "s", "trigger": TRIGGER}
        raw, _ = _drive(case, [], script=[AIMessage(content="不需要工具,直接答复。")])
        # judges 直接迭代 tool_calls / tool_results——必须是空列表,不能是 None
        self.assertEqual(raw["tool_calls"], [])
        self.assertEqual(raw["tool_results"], [])
        self.assertEqual(raw["reply"], "不需要工具,直接答复。")

    def test_mid_turn_exception_propagates(self):
        """中途故障原样上抛:runner 的单用例 try/except 依赖此契约,
        适配器不得吞异常改返空轨迹(那会把基础设施故障伪装成拦截失败)"""
        script = [
            AIMessage(
                content="",
                tool_calls=[{"name": "fake_probe", "args": {"query": "x"},
                             "id": "call_1", "type": "tool_call"}],
            ),
            RuntimeError("模拟回合中途故障"),
        ]
        case = {"id": "boom", "surface": "s", "trigger": TRIGGER}
        with self.assertRaises(RuntimeError) as ctx:
            _drive(case, [], script=script)
        self.assertEqual(str(ctx.exception), "模拟回合中途故障")


if __name__ == '__main__':
    unittest.main()
