"""TUI 适配器表征测试(会话引擎 03 票)。

entry/main.py 的 agent_worker 从手写 astream 解析切到消费 SessionEngine
回合事件;本文件钉住事件→行为映射,与旧解析逐分支等价:
- ToolCall → spinner 工具态 + 打印工具名
- ToolResult → spinner 回思考态,不打印;审批门拒绝原文照印(2026-08-27
  真机发现:模型的转述会复读 thread 历史里的旧话术,操作员须直读门对
  agent 说了什么,不赌转述)
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
    ApprovalRequest,
    ToolCall,
    ToolResult,
    Reply,
    TurnEnd,
    TurnTrajectory,
)
from auditronclaw.core.approval.gate import REJECT_PHRASE

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

    def test_gate_rejection_tool_result_printed_verbatim(self):
        """审批门拒绝的 tool_result 原文照印:tool_result 如实、模型转述却
        复读历史旧话术("无人值守…"两例逐字雷同,2026-08-27 真机审计定位),
        操作员须直读门对 agent 说了什么——拒绝分案话术照印,转述漂移时
        有据可对;普通工具回传仍不打印"""
        rejection = (f"❌ {REJECT_PHRASE}：工具 write_office_file 的本次调用属于"
                     "必批副作用（write：写类副作用）。操作员已明确拒绝本次调用，未执行。")
        spinner, calls = _drive([
            ToolCall(name="write_office_file", args={}),
            ToolResult(tool="write_office_file", result=rejection),
        ])
        self.assertFalse(spinner.is_tool_calling, "工具回传:spinner 回思考态")
        self.assertEqual(calls, [
            ("  ●\033[38;5;51m Tool Call: \033[0mwrite_office_file",),
            ('',),
            (f"  \033[31m{rejection}\033[0m",),
            ('',),
        ], "拒绝原文整条照印(红字),后随行距空行")

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

    def test_approval_request_prints_block_and_pauses_tool_mode(self):
        """审批打断事件:打印审批块(完整参数+风险级,04 票),spinner 退出
        工具态转等人——回合未收尾,is_spinning 不动"""
        spinner = FakeSpinner()
        spinner.is_tool_calling = True
        calls = []
        request = ApprovalRequest(
            tool="write_office_file",
            args={"filepath": "reports/daily.md", "content": "x"},
            risk_class="write", reason="写类副作用(目标:reports/daily.md)")
        with patch.object(tui_main, 'cprint', side_effect=lambda *a, **k: calls.append(a)):
            tui_main.handle_turn_event(request, spinner)
        self.assertFalse(spinner.is_tool_calling, "审批等待:spinner 退出工具态")
        self.assertTrue(spinner.is_spinning, "回合未收尾,spinner 不停")
        flat = "".join("".join(map(str, c)) for c in calls)
        self.assertIn("write_office_file", flat)
        self.assertIn("write", flat)
        self.assertIn("reports/daily.md", flat)


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


class TestModuleHealth(unittest.TestCase):
    """模块级健康哨兵:async_main 不被自动化执行,它引用的模块级名字
    必须存在——03 票改 import 块丢掉 DB_PATH,真机一跑才炸(2026-08-27
    04 票手工清单发现),本哨兵补上这道缝。"""

    def test_async_main_globals_resolve(self):
        import entry.main as m
        for name in ("DB_PATH", "create_agent_app", "SessionEngine",
                     "pacemaker_loop", "task_queue", "TurnRequest"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(m, name), f"async_main 引用的 {name} 不在模块名空间")


if __name__ == '__main__':
    unittest.main()
