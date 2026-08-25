"""TUI 适配器表征测试(会话引擎 03 票)。

entry/main.py 的 agent_worker 从手写 astream 解析切到消费 SessionEngine
回合事件;本文件钉住事件→行为映射,与旧解析逐分支等价:
- ToolCall → spinner 工具态 + 打印工具名
- ToolResult → spinner 回思考态,不打印
- Reply(final) → 停 spinner + 打印(多行缩进格式照旧)
- Reply(非 final,content 与 tool_calls 并存) → 不显示(保现状)
- TurnEnd → 停 spinner + 行距收尾

映射钉在模块级 handle_turn_event(事件→行为,纯适配逻辑);队列循环与
/exit 处理、异常捕获留在 agent_worker 闭包内,由手工清单四场景验收
(纯回复 / 单工具 / 多工具 / 异常)。
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.session import (
    ToolCall,
    ToolResult,
    Reply,
    TurnEnd,
    TurnTrajectory,
)

import entry.main as tui_main


# ============ 假件:鸭子型 spinner(真 SpinnerState 是 async_main 闭包内类) ============

class FakeSpinner:
    def __init__(self):
        self.is_spinning = True
        self.is_tool_calling = False
        self.tool_msg = ""


def _drive(events):
    """喂一段事件流给映射函数,返回 (spinner 终态, cprint 调用序)。"""
    spinner = FakeSpinner()
    calls = []
    with patch.object(tui_main, 'cprint', side_effect=lambda *a, **k: calls.append(a)):
        for ev in events:
            tui_main.handle_turn_event(ev, spinner)
    return spinner, calls


class TestEventBehaviorMapping(unittest.TestCase):
    """单事件钉子:每个回合事件各自由旧解析的哪一分支翻译而来。"""

    def test_tool_call_sets_tool_mode_and_prints_name(self):
        spinner, calls = _drive([ToolCall(name="read_file", args={"path": "a.md"})])
        self.assertTrue(spinner.is_tool_calling, "工具调用:spinner 进工具态")
        self.assertEqual(spinner.tool_msg, "唤醒内置工具 : read_file...")
        self.assertEqual(calls, [("  ●\033[38;5;51m Tool Call: \033[0mread_file",), ('',)])

    def test_tool_result_returns_to_thinking_mode_silently(self):
        spinner, calls = _drive([
            ToolCall(name="read_file", args={}),
            ToolResult(tool="read_file", result="内容"),
        ])
        self.assertFalse(spinner.is_tool_calling, "工具回传:spinner 回思考态")
        self.assertTrue(spinner.is_spinning, "回合未收尾,spinner 不停")
        self.assertEqual(len(calls), 2, "工具回传本身不打印(保现状)")

    def test_final_reply_stops_spinner_and_prints_formatted(self):
        spinner, calls = _drive([Reply(content="第一行\n第二行", final=True)])
        self.assertFalse(spinner.is_spinning, "final reply:停 spinner")
        self.assertEqual(calls, [(
            "  \033[38;5;141m❯\033[0m \033[38;5;250m第一行\n    第二行\033[0m",
        )], "多行缩进格式照旧")

    def test_non_final_reply_not_displayed(self):
        """content 与 tool_calls 并存的消息:旧解析 if/elif 只走工具分支,
        文本不显示——非 final Reply 对应此现状,spinner 也不停"""
        spinner, calls = _drive([Reply(content="先探测一下。", final=False)])
        self.assertEqual(calls, [], "非 final Reply 不显示")
        self.assertTrue(spinner.is_spinning, "并存消息不收尾回合,spinner 不停")

    def test_turn_end_stops_spinner_and_prints_blank_line(self):
        spinner, calls = _drive([TurnEnd(trajectory=TurnTrajectory([], [], ""))])
        self.assertFalse(spinner.is_spinning)
        self.assertEqual(calls, [()], "行距收尾(旧代码回合末的无参空行 cprint())")


class TestFullTurnSequence(unittest.TestCase):
    """整回合钉子:与 01 票等价性测试同一条事件流,打印序与旧 TUI 逐行一致。

    旧手写解析在此流上的输出:两条 Tool Call 行(各带一行空行)+ final
    reply 格式化输出 + 回合末空行;并存的"先探测一下。"不显示。
    """

    def test_multi_tool_turn_prints_exactly_as_legacy(self):
        events = [
            ToolCall(name="fake_probe", args={"query": "dir"}),
            Reply(content="先探测一下。", final=False),
            ToolResult(tool="fake_probe", result="probe-ok:dir"),
            ToolCall(name="fake_check", args={"target": "report"}),
            ToolResult(tool="fake_check", result="check-ok:report"),
            Reply(content="探测与核对都完成了。", final=True),
            TurnEnd(trajectory=TurnTrajectory(
                [{"tool": "fake_probe", "args": {"query": "dir"}}],
                [{"tool": "fake_probe", "result": "probe-ok:dir"}],
                "先探测一下。\n探测与核对都完成了。",
            )),
        ]
        spinner, calls = _drive(events)

        self.assertEqual(calls, [
            ("  ●\033[38;5;51m Tool Call: \033[0mfake_probe",),
            ('',),
            ("  ●\033[38;5;51m Tool Call: \033[0mfake_check",),
            ('',),
            ("  \033[38;5;141m❯\033[0m \033[38;5;250m探测与核对都完成了。\033[0m",),
            (),  # TurnEnd 行距收尾:旧代码回合末的 cprint() 无参空行
        ])
        self.assertFalse(spinner.is_spinning)
        self.assertFalse(spinner.is_tool_calling, "final reply 后停在思考态")


if __name__ == '__main__':
    unittest.main()
