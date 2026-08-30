"""TUI 审批交互(04 票):回合内审批提示、输入冲突仲裁、规则管理面。

自动化覆盖应答器注入路径(spec Testing Decisions:「TUI 审批交互:手工清单
验收,自动化测应答器注入路径」):
- 纯函数:parse_approval_answer 三选项映射、format_approval_block 完整参数、
  规则清单表与 id 前缀匹配、决定回显
- 应答桥仲裁(输入冲突的确定行为):主提示提交的行永远排队成下一回合,
  审批答案只来自审批提示;引擎超时留下的死条目即时出桥(状态条同拍收回);
  问人途中条目死则收回提示不再等人;输入循环退出时
  挂起审批按无人拒收尾(单 worker 队列不挂死)
- 读答案循环:无效输入重问;Ctrl+C/Ctrl+D 一律拒(fail-closed)
- 端到端(假 LLM 经 TUI 桥):心跳来源永不问人(无交互直接拒);批准一次/
  永久允许/拒绝三答法;永久允许铸规则且此后同调用静默;退出收尾不堵回合

先例:tests/test_tui_adapter.py(事件映射钉法)、tests/test_approval_interrupt.py(假件)。
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval.gate import (
    DecisionSource,
    REJECT_PHRASE,
    ApprovalDecision,
    TurnOrigin,
)
from auditronclaw.core.approval.rules import RuleStore
from auditronclaw.core.bus import TurnRequest
from auditronclaw.core.session import (
    ApprovalRequest,
    SessionEngine,
    ToolResult,
    TurnEnd,
)

import entry.main as tui_main


def _run(coro):
    return asyncio.run(coro)


def _req(**override):
    """构造审批请求载荷(门 interrupt 载荷的字段形状)。"""
    fields = dict(tool="write_office_file",
                  args={"filepath": "reports/daily.md", "content": "落盘内容"},
                  risk_class="write",
                  reason="写类副作用(目标:reports/daily.md)")
    fields.update(override)
    return ApprovalRequest(**fields)


def _flat(calls):
    """把捕获的 cprint 调用拼成一段纯文本(断言用)。"""
    return "".join("".join(map(str, c)) for c in calls)


async def _wait_until(cond, timeout=5.0):
    """轮询等待条件成立(等异步假件就位,超时即测试失败)。"""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() >= deadline:
            raise TimeoutError("条件等待超时:异步假件未在预期时间内就位")
        await asyncio.sleep(0.01)


# ============ 纯函数:选项解析与审批块 ============

class TestParseApprovalAnswer(unittest.TestCase):
    """y/a/n 三选项 → ApprovalDecision 映射(大小写与空白归一,其余不猜)。"""

    def test_y_approves_once(self):
        for text in ("y", "Y", "yes", "  y "):
            with self.subTest(text=text):
                d = tui_main.parse_approval_answer(text)
                self.assertIs(d.approved, True)
                self.assertIs(d.persist, False)
                self.assertIs(d.source, DecisionSource.USER_ONCE)

    def test_a_approves_and_persists(self):
        for text in ("a", "A", "always"):
            with self.subTest(text=text):
                d = tui_main.parse_approval_answer(text)
                self.assertIs(d.approved, True)
                self.assertIs(d.persist, True)
                self.assertIs(d.source, DecisionSource.USER_PERSIST)

    def test_n_denies(self):
        for text in ("n", "N", "no"):
            with self.subTest(text=text):
                d = tui_main.parse_approval_answer(text)
                self.assertIs(d.approved, False)
                self.assertIs(d.persist, False)
                self.assertIs(d.source, DecisionSource.USER_ONCE)

    def test_anything_else_is_invalid(self):
        """无效输入不是"默认拒绝"而是"没听懂":返回 None 由读循环重问"""
        for text in ("", "x", "好的", "ya", "y a", "批准"):
            with self.subTest(text=text):
                self.assertIsNone(tui_main.parse_approval_answer(text))


class TestFormatApprovalBlock(unittest.TestCase):
    """审批块:完整参数 + 风险级 + 依据——操作员批的是具体动作,不是类别印象。"""

    def test_block_shows_tool_risk_reason_and_full_args(self):
        block = tui_main.format_approval_block(_req())
        self.assertIn("write_office_file", block)
        self.assertIn("write", block)          # 风险级
        self.assertIn("reports/daily.md", block)  # 依据里的目标
        # 完整参数:键值都在,多字节不转义(命令行/路径/域名原样可读)
        self.assertIn('"filepath"', block)
        self.assertIn('"reports/daily.md"', block)
        self.assertIn("落盘内容", block)
        self.assertNotIn("\\u", block)

    def test_block_shows_command_args_for_shell_risk(self):
        block = tui_main.format_approval_block(_req(
            tool="execute_office_shell",
            args={"command": "python scripts/run.py > out.txt"},
            risk_class="execute", reason="必批命令段:python scripts/run.py > out.txt"))
        self.assertIn("python scripts/run.py > out.txt", block)
        self.assertIn("execute", block)


# ============ 应答桥:输入冲突的确定行为 ============

class TestApprovalBridgeArbitration(unittest.TestCase):
    """终端输入单持有者是输入循环;主提示的行排队,审批答案只来自审批提示。"""

    def test_pending_line_queues_as_next_turn_not_as_answer(self):
        """冲突规则本尊:审批挂起时主提示提交的行 → 下一回合队列(不被审批吃掉);
        审批答案只来自应答步读到的审批提示。"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()
            responder_task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)

            queue = asyncio.Queue()
            stop = await tui_main.process_user_line("好的,回头见", queue, _store())
            reads = []

            async def read_answer(req):
                reads.append(req)
                return ApprovalDecision(True, False, DecisionSource.USER_ONCE)

            with patch.object(tui_main, 'cprint'):
                await tui_main.answer_pending_approvals(bridge, read_answer)
            decision = await responder_task
            return queue, reads, decision, stop

        queue, reads, decision, stop = _run(scenario())
        self.assertEqual(queue.get_nowait(),
                         TurnRequest(text="好的,回头见", origin=TurnOrigin.HUMAN),
                         "主提示的行排队成下一回合信封")
        self.assertFalse(stop)
        self.assertEqual(reads, [_req()], "审批答案只经应答步的读入")
        self.assertIs(decision.approved, True)
        self.assertIs(decision.source, DecisionSource.USER_ONCE)

    def test_no_pending_is_noop(self):
        """无挂起审批:应答步零动作(不读输入)"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()

            async def read_answer(req):
                raise AssertionError("无挂起审批不得读输入")

            await tui_main.answer_pending_approvals(bridge, read_answer)
            return bridge.pending

        self.assertFalse(_run(scenario()))

    def test_dead_entry_from_timeout_is_skipped(self):
        """引擎超时放弃应答器(future 被取消):死条目即时出桥,应答步无事
        可做——不问人、不补失效提示(失效已由状态条收回与引擎拒绝回复呈现)"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()
            task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)
            task.cancel()  # wait_for 超时掐死应答器的形状
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.01)

            async def read_answer(req):
                raise AssertionError("死条目不得问人")

            await tui_main.answer_pending_approvals(bridge, read_answer)
            return bridge.pending

        self.assertFalse(_run(scenario()), "死条目不滞留,不留残挂起")

    def test_dead_entry_clears_pending_without_drain(self):
        """死条目即时出桥:引擎超时掐死 future 的同一拍,挂起态即消——
        状态条不得等到操作员下次提交才收回"审批等待应答"
        (04 票真机两轮观察推翻 code-review 的'有界滞留'裁定)"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()
            task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)
            task.cancel()  # wait_for 超时掐死应答器的形状
            try:
                await task
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.01)  # done 回调让出一拍
            return bridge.pending

        self.assertFalse(_run(scenario()),
                         "future 已死,挂起态必须即时消失")

    def test_timeout_abandons_hanging_approval_prompt(self):
        """操作员已切到审批提示再沉默:引擎超时后提示不再等人,读到一半的
        答案通道随之作废——超时即终局拒绝,提示层不得比引擎活得久
        (04 票手工清单场景 4 真机发现)"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()
            task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)

            cancelled = []

            async def hanging_read(req):
                try:
                    await asyncio.Event().wait()  # 操作员在审批提示处沉默
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            async def engine_timeout_like():
                await asyncio.sleep(0.05)
                task.cancel()  # 引擎超时掐死应答器(future 一并取消)

            timeout_task = asyncio.create_task(engine_timeout_like())
            calls = []
            with patch.object(tui_main, 'cprint',
                              side_effect=lambda *a, **k: calls.append(a)):
                await asyncio.wait_for(
                    tui_main.answer_pending_approvals(bridge, hanging_read),
                    timeout=3)
            try:
                await task
            except asyncio.CancelledError:
                pass
            await timeout_task
            return bridge.pending, cancelled, calls

        pending, cancelled, calls = _run(scenario())
        self.assertFalse(pending, "死条目排空,不留残挂起")
        self.assertEqual(cancelled, [True], "挂在提示上的读被取消,不泄漏")
        self.assertTrue(any("超时" in "".join(map(str, c)) for c in calls),
                        "给操作员一行失效说明")

    def test_exit_close_denies_pending(self):
        """输入循环退出(/exit、Ctrl+C):挂起审批一律按无人拒,回合必能收尾"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()
            task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)
            denied = bridge.close()
            decision = await task
            return denied, decision

        denied, decision = _run(scenario())
        self.assertEqual(denied, 1)
        self.assertEqual(decision,
                         ApprovalDecision(False, False, DecisionSource.UNATTENDED))
        self.assertFalse(_run(_async_pending_after_close()))

    def test_late_approval_after_exit_denied_by_drain_loop(self):
        """退出后的迟到审批:回合在输入循环退出后才弹出审批,收尾循环逐拍
        按无人拒——不等审批超时(默认 5 分钟),退出不被拖长(code-review 修)"""
        async def scenario():
            bridge = tui_main.ApprovalBridge()

            async def turn_like():
                await asyncio.sleep(0.05)      # 回合仍在跑(队列未消费完)
                return await bridge.responder(_req())  # 阻塞在审批上

            turn = asyncio.create_task(turn_like())
            denied = await tui_main.drain_bridge_until(bridge, turn)
            decision = await turn
            return denied, decision

        start = time.monotonic()
        denied, decision = _run(scenario())
        elapsed = time.monotonic() - start
        self.assertEqual(denied, 1)
        self.assertEqual(decision,
                         ApprovalDecision(False, False, DecisionSource.UNATTENDED))
        self.assertLess(elapsed, 5, "收尾拍(0.25s)即拒,不得等审批超时")

    def test_answer_echoes_decision(self):
        """应答步回显决定:操作员看得见自己的批复成了什么"""
        async def scenario(decision):
            bridge = tui_main.ApprovalBridge()
            task = asyncio.create_task(bridge.responder(_req()))
            await _wait_until(lambda: bridge.pending)
            calls = []
            with patch.object(tui_main, 'cprint',
                              side_effect=lambda *a, **k: calls.append(a)):
                await tui_main.answer_pending_approvals(
                    bridge, lambda req: _nowait(decision))
            await task
            return calls

        for decision, expect in (
            (ApprovalDecision(True, False, DecisionSource.USER_ONCE), "已批准"),
            (ApprovalDecision(True, True, DecisionSource.USER_PERSIST), "永久允许"),
            (ApprovalDecision(False, False, DecisionSource.USER_ONCE), "已拒绝"),
        ):
            with self.subTest(source=decision.source):
                calls = _run(scenario(decision))
                self.assertTrue(any(expect in "".join(map(str, c)) for c in calls),
                                f"回显应含 {expect!r}: {calls}")


async def _nowait(decision):
    return decision


async def _async_pending_after_close():
    bridge = tui_main.ApprovalBridge()
    task = asyncio.create_task(bridge.responder(_req()))
    await _wait_until(lambda: bridge.pending)
    bridge.close()
    await task
    return bridge.pending


# ============ 读答案循环:重问与中断 ============

class _ScriptedPrompt:
    """鸭子型 prompt(message) -> 文本:按脚本逐条作答,记录提示消息。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):  # KeyboardInterrupt 不是 Exception
            raise reply
        return reply


class TestPromptApprovalDecision(unittest.TestCase):
    """审批提示读答案:无效输入重问;Ctrl+C/Ctrl+D 一律拒(fail-closed)。"""

    def test_invalid_then_valid_reasks(self):
        prompt = _ScriptedPrompt(["x", "y"])
        with patch.object(tui_main, 'cprint'):
            decision = _run(tui_main.prompt_approval_decision(_req(), prompt))
        self.assertIs(decision.approved, True)
        self.assertIs(decision.source, DecisionSource.USER_ONCE)
        self.assertEqual(len(prompt.messages), 2, "无效输入重问一次")
        self.assertIn("write_office_file", prompt.messages[0])
        self.assertTrue(all("y" in m and "a" in m and "n" in m for m in prompt.messages),
                        "提示消息始终带三选项")

    def test_keyboard_interrupt_denies(self):
        prompt = _ScriptedPrompt([KeyboardInterrupt()])
        decision = _run(tui_main.prompt_approval_decision(_req(), prompt))
        self.assertIs(decision.approved, False)
        self.assertIs(decision.source, DecisionSource.USER_ONCE)

    def test_eof_denies(self):
        prompt = _ScriptedPrompt([EOFError()])
        decision = _run(tui_main.prompt_approval_decision(_req(), prompt))
        self.assertIs(decision.approved, False)
        self.assertIs(decision.source, DecisionSource.USER_ONCE)


# ============ 规则管理面:/rules 与 /revoke ============

def _store(*entries):
    """临时位规则存取,预铸条目 [(action, scope, source)](铸规则留痕 patch 掉)。"""
    store = RuleStore(path=os.path.join(tempfile.mkdtemp(prefix="tui04_store_"),
                                        "approval_rules.json"))
    with patch('auditronclaw.core.logger._audit_logger'):
        for action, scope, source in entries:
            store.persist_rule(action=action, scope=scope, source=source)
    return store


class TestOperatorCommands(unittest.TestCase):
    """/rules 清单展示(含 source/created_at)与 /revoke 前缀撤销。"""

    def _run_command(self, text, store):
        calls = []
        with patch('auditronclaw.core.logger._audit_logger'), \
             patch.object(tui_main, 'cprint', side_effect=lambda *a, **k: calls.append(a)):
            consumed = tui_main.handle_operator_command(text, store)
        return consumed, calls

    def test_rules_lists_entries_with_source_and_created_at(self):
        store = _store(("write", "office/reports/**", "approval"),
                       ("execute", "office/scripts/**", "cli"))
        consumed, calls = self._run_command("/rules", store)
        self.assertTrue(consumed)
        self.assertIn("write", _flat(calls))
        self.assertIn("office/reports/**", _flat(calls))
        self.assertIn("approval", _flat(calls))
        self.assertIn("cli", _flat(calls))
        self.assertIn("execute", _flat(calls))
        self.assertRegex(_flat(calls), r"\d{4}-\d{2}-\d{2}", "铸成时间可见")

    def test_rules_empty_store_shows_cold_start_note(self):
        consumed, calls = self._run_command("/rules", _store())
        self.assertTrue(consumed)
        self.assertIn("0", _flat(calls))

    def test_revoke_by_id_prefix(self):
        store = _store(("write", "office/reports/**", "approval"))
        rules = store.list_rules()
        self.assertEqual(len(rules), 1)
        prefix = rules[0].id[:6]
        with patch('auditronclaw.core.logger._audit_logger'):
            consumed, calls = self._run_command(f"/revoke {prefix}", store)
        self.assertTrue(consumed)
        self.assertEqual(store.list_rules(), [], "撤销即失效(下次匹配读不到)")
        self.assertIn("office/reports/**", _flat(calls))

    def test_revoke_ambiguous_prefix_lists_candidates(self):
        """共享前缀撞多条:不猜,列出候选让操作员补长前缀"""
        store = _store()
        with open(store.path, "w", encoding="utf-8") as f:
            json.dump([
                {"id": "aa11bb22cc", "action": "write", "scope": "office/reports/**",
                 "source": "approval", "created_at": "2026-08-27T00:00:00Z"},
                {"id": "aa22cc33dd", "action": "write", "scope": "office/notes/**",
                 "source": "approval", "created_at": "2026-08-27T00:00:00Z"},
            ], f)
        consumed, calls = self._run_command("/revoke aa", store)
        self.assertTrue(consumed)
        self.assertEqual(len(store.list_rules()), 2, "歧义前缀不得误撤")
        self.assertTrue(any("歧义" in "".join(map(str, c)) for c in calls),
                        f"应提示歧义: {_flat(calls)}")

    def test_revoke_unknown_prefix_keeps_rules(self):
        store = _store(("write", "office/reports/**", "approval"))
        consumed, _calls = self._run_command("/revoke deadbeef", store)
        self.assertTrue(consumed)
        self.assertEqual(len(store.list_rules()), 1)

    def test_revoke_without_argument_shows_usage(self):
        consumed, calls = self._run_command("/revoke", _store())
        self.assertTrue(consumed, "/revoke 无参也是被消费的命令,不得当消息发给 agent")

    def test_normal_message_not_consumed(self):
        queue = asyncio.Queue()

        async def scenario():
            return await tui_main.process_user_line("写一份日报", queue, _store())

        self.assertFalse(_run(scenario()))
        self.assertEqual(queue.get_nowait(),
                         TurnRequest(text="写一份日报", origin=TurnOrigin.HUMAN))

    def test_rules_command_does_not_queue_turn(self):
        """管理面命令就地消费:不入回合队列,不惊动 agent"""
        queue = asyncio.Queue()

        async def scenario():
            with patch.object(tui_main, 'cprint'):
                return await tui_main.process_user_line("/rules", queue, _store())

        self.assertFalse(_run(scenario()))
        self.assertTrue(queue.empty())

    def test_exit_still_queues_control_token(self):
        queue = asyncio.Queue()

        async def scenario():
            return await tui_main.process_user_line("/exit", queue, _store())

        self.assertTrue(_run(scenario()), "/exit 返回退出标志")
        self.assertEqual(queue.get_nowait().text, "/exit")


class TestRuleIdPrefixMatch(unittest.TestCase):
    """id 前缀匹配纯函数:唯一命中/歧义/未命中三态。"""

    def test_unique_ambiguous_and_miss(self):
        from auditronclaw.core.approval.rules import ApprovalRule
        rules = [
            ApprovalRule(id="aa11bb22", action="write", scope="s1",
                         source="approval", created_at="2026-08-27T00:00:00Z"),
            ApprovalRule(id="aa22cc33", action="write", scope="s2",
                         source="approval", created_at="2026-08-27T00:00:00Z"),
        ]
        self.assertEqual([r.id for r in tui_main.match_rule_id_prefix(rules, "aa11")],
                         ["aa11bb22"])
        self.assertEqual(len(tui_main.match_rule_id_prefix(rules, "aa")), 2)
        self.assertEqual(tui_main.match_rule_id_prefix(rules, "zz99"), [])


# ============ 端到端(假 LLM 经 TUI 桥):三答法与心跳无交互 ============

def _write_stub(calls):
    def run(filepath: str, content: str, mode: str = "w") -> str:
        calls.append(dict(filepath=filepath, content=content, mode=mode))
        return f"written:{filepath}"
    return StructuredTool.from_function(
        func=run, name="write_office_file", description="测试桩:写工具")


def _delete_stub(calls):
    def run(task_id: str) -> str:
        calls.append(dict(task_id=task_id))
        return f"deleted:{task_id}"
    return StructuredTool.from_function(
        func=run, name="delete_scheduled_task", description="测试桩:删工具")


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_approval_interrupt)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽:回合步数超出脚本覆盖"
        return self.script.pop(0)


def _call(call_id: str, tool: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": tool, "args": args, "id": call_id, "type": "tool_call"}])


def _build_app(stack: ExitStack, script, tools):
    """按现有注入点构造 agent app;规则文件钉到临时工作区,返回 (app, 工作区)。"""
    from auditronclaw.core.agent import create_agent_app
    from auditronclaw.core.config import WorkspaceConfig
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="tui04_ws_"))
    workspace.ensure_dirs()
    stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                              return_value=ScriptedLLM(script)))
    stack.enter_context(patch('auditronclaw.core.agent.build_builtin_tools',
                              return_value=tools))
    stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                              return_value=[]))
    stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
    app = create_agent_app(provider_name="fake", model_name="fake-model",
                           workspace=workspace,
                           checkpointer=MemorySaver(), thread_id="tui04")
    return app, workspace.approval_rules_file


async def _collect(agen, events):
    async for ev in agen:
        events.append(ev)


async def _run_turn_answered(engine, bridge, read, text="写一份日报"):
    """起一个人来源回合,等审批挂起后经 TUI 应答步作答,收全事件。

    cprint 打补丁(应答步回显走它;headless 环境下真打印会报错,同因见
    03-TUI 票的 NoConsoleScreenBufferError 红点)。
    """
    events = []
    task = asyncio.create_task(
        _collect(engine.run_turn(text, origin=TurnOrigin.HUMAN), events))
    await _wait_until(lambda: bridge.pending)
    with patch.object(tui_main, 'cprint'):
        await tui_main.answer_pending_approvals(bridge, read)
    await task
    return events


class TestTuiBridgeEndToEnd(unittest.TestCase):
    """TUI 桥注入引擎后的三答法:批准执行、永久允许铸规则、拒绝返回 agent。"""

    def test_approve_once_executes_the_call(self):
        calls = []
        script = [
            _call("c1", "write_office_file",
                  {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已写完。"),
        ]
        read = lambda req: _nowait(
            ApprovalDecision(True, False, DecisionSource.USER_ONCE))
        with ExitStack() as stack:
            app, _rules_path = _build_app(stack, script, [_write_stub(calls)])
            bridge = tui_main.ApprovalBridge()
            engine = SessionEngine(app, "tui04", approval_responder=bridge.responder, approval_timeout=10)
            events = _run(_run_turn_answered(engine, bridge, read))

        self.assertEqual([c["filepath"] for c in calls], ["reports/daily.md"],
                         "批准一次:原调用执行")
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertIn("written:reports/daily.md", results[0].result)
        self.assertIsInstance(events[-1], TurnEnd)

    def test_persist_mints_rule_then_next_turn_silent(self):
        calls = []
        script = [
            _call("c1", "write_office_file",
                  {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="第一回合完成。"),
            _call("c2", "write_office_file",
                  {"filepath": "reports/daily.md", "content": "y"}),
            AIMessage(content="第二回合完成。"),
        ]
        read = lambda req: _nowait(
            ApprovalDecision(True, True, DecisionSource.USER_PERSIST))

        async def scenario(app, bridge, engine):
            first = await _run_turn_answered(engine, bridge, read)
            second_events = []
            await _collect(engine.run_turn("再写一次", origin=TurnOrigin.HUMAN),
                           second_events)
            return first, second_events

        with ExitStack() as stack:
            app, rules_path = _build_app(stack, script, [_write_stub(calls)])
            bridge = tui_main.ApprovalBridge()
            engine = SessionEngine(app, "tui04", approval_responder=bridge.responder, approval_timeout=10)
            first, second = _run(scenario(app, bridge, engine))

        self.assertEqual(len(calls), 2, "永久允许:本次执行,下次静默执行")
        with open(rules_path, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(entries[0]["action"], "write")
        self.assertEqual(entries[0]["scope"], "office/reports/daily.md")
        self.assertEqual(entries[0]["source"], "approval")
        self.assertTrue(any(isinstance(e, ApprovalRequest) for e in first))
        self.assertFalse(any(isinstance(e, ApprovalRequest) for e in second),
                         "规则已铸,同调用不再问人")

    def test_deny_returns_rejection_to_agent(self):
        calls = []
        script = [
            _call("c1", "write_office_file",
                  {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="未获批准,已放弃。"),
        ]
        read = lambda req: _nowait(
            ApprovalDecision(False, False, DecisionSource.USER_ONCE))
        with ExitStack() as stack:
            app, _rules_path = _build_app(stack, script, [_write_stub(calls)])
            bridge = tui_main.ApprovalBridge()
            engine = SessionEngine(app, "tui04", approval_responder=bridge.responder, approval_timeout=10)
            events = _run(_run_turn_answered(engine, bridge, read))

        self.assertEqual(calls, [], "被拒的调用不得触达原工具")
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertIn(REJECT_PHRASE, results[0].result,
                      "拒绝作为 tool_result 返回 agent,回合内收尾")
        self.assertIn("操作员", results[0].result,
                      "人拒的话术说操作员拒")
        self.assertNotIn("无人值守", results[0].result,
                         "操作员在场刚拒,不得谎称无人值守(真机发现)")
        self.assertIsInstance(events[-1], TurnEnd)

    def test_heartbeat_never_prompts_through_tui_bridge(self):
        """心跳来源回合含高危动作:经 TUI 桥也不弹提示——直接拒,无交互"""
        calls = []
        script = [
            _call("c1", "delete_scheduled_task", {"task_id": "desk01"}),
            AIMessage(content="已处理。"),
        ]
        with ExitStack() as stack:
            app, _rules_path = _build_app(stack, script, [_delete_stub(calls)])
            bridge = tui_main.ApprovalBridge()
            engine = SessionEngine(app, "tui04", approval_responder=bridge.responder, approval_timeout=10)

            async def scenario():
                events = []
                await _collect(engine.run_turn("【系统内部心跳触发】\n跑一轮事务台",
                                               origin=TurnOrigin.HEARTBEAT), events)
                # 回合已收尾后才检查应答步:若心跳弹了提示,这里 pending 会为真
                async def read(req):
                    raise AssertionError("心跳来源回合不得问人")
                await tui_main.answer_pending_approvals(bridge, read)
                return events

            events = _run(scenario())

        self.assertEqual(calls, [], "无人且无规则:高危删除直接拒")
        self.assertFalse(any(isinstance(e, ApprovalRequest) for e in events))
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertIn(REJECT_PHRASE, results[0].result)
        self.assertIsInstance(events[-1], TurnEnd)

    def test_exit_close_unblocks_pending_turn(self):
        """退出收尾:输入循环已死,挂起审批按无人拒,回合照常收尾不挂死"""
        calls = []
        script = [
            _call("c1", "write_office_file",
                  {"filepath": "reports/daily.md", "content": "x"}),
            AIMessage(content="已放弃。"),
        ]

        async def scenario(app, bridge, engine):
            events = []
            task = asyncio.create_task(
                _collect(engine.run_turn("写日报", origin=TurnOrigin.HUMAN), events))
            await _wait_until(lambda: bridge.pending)
            bridge.close()
            await task
            return events

        with ExitStack() as stack:
            app, _rules_path = _build_app(stack, script, [_write_stub(calls)])
            bridge = tui_main.ApprovalBridge()
            engine = SessionEngine(app, "tui04", approval_responder=bridge.responder, approval_timeout=10)
            events = _run(scenario(app, bridge, engine))

        self.assertEqual(calls, [])
        results = [e for e in events if isinstance(e, ToolResult)]
        self.assertIn(REJECT_PHRASE, results[0].result)
        self.assertIsInstance(events[-1], TurnEnd, "回合收尾,单 worker 队列不挂死")


if __name__ == '__main__':
    unittest.main()
