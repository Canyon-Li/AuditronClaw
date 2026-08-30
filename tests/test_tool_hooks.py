"""工具调用 hooks 与 DomainDenied 统一拒绝回执（03 票 C）。

机制在包装单点长出：wrap_tool 内层以 for 循环调 hooks（before / after /
on_error），hooks 只观察与记录、无否决权——否决权仍只属审批门；遥测
（耗时/成败）走 LangChain callbacks，不在此落。

- 无 hooks 时行为不变：由既有门测试（test_approval_gate）整体钉住，
  本文件显式再钉一条 hooks=() 直通
- DomainDenied：工具体内 require_domain 抛出，wrapper 统一格式落拒绝
  回执——回执与手写三件套时代逐字一致
- AuditReceiptHook：成功回执单源（返回值携带 Receipt，hook 落
  system 级审计事件，工具体不再手写回执）
"""
import asyncio
import inspect
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langchain_core.tools import StructuredTool

from auditronclaw.core.approval.classifier import RISK_READ
from auditronclaw.core.approval.gate import TurnOrigin, wrap_all_tools, wrap_tool
from auditronclaw.core.approval.hooks import (
    AuditReceiptHook,
    Receipt,
    ToolCallContext,
)
from auditronclaw.core.tools.domain_gate import DomainDenied, require_domain


def _calc_like(func, name="calculator"):
    """分类册里的纯读名（免批直通），工具体由用例给定。"""
    return StructuredTool.from_function(func=func, name=name,
                                        description="测试桩：纯读")


class RecordingHook:
    """记录每次观察的假 hook（before/after/on_error 三观察位）。"""

    def __init__(self):
        self.before_calls = []
        self.after_calls = []
        self.error_calls = []

    def before(self, ctx):
        self.before_calls.append(ctx)

    def after(self, ctx, result):
        self.after_calls.append((ctx, result))
        return result

    def on_error(self, ctx, exc):
        self.error_calls.append((ctx, exc))
        raise exc


class TestHookLifecycle(unittest.TestCase):
    """hooks 生命周期：before 见调用上下文，after 见结果，on_error 见异常。"""

    def test_success_runs_before_then_after_with_context(self):
        """成功路径：before/after 依序各一次，ctx 携带工具名/参数/来源/分级"""
        hook = RecordingHook()
        gated = wrap_tool(_calc_like(lambda x: x * 2), thread_id="hook_test",
                          hooks=(hook,))
        result = gated.invoke({"x": 3}, config={
            "configurable": {"thread_id": "hook_test", "turn_origin": "human"}})
        self.assertEqual(result, 6)
        self.assertEqual(len(hook.before_calls), 1)
        ctx = hook.before_calls[0]
        self.assertIsInstance(ctx, ToolCallContext)
        self.assertEqual(ctx.tool, "calculator")
        self.assertEqual(ctx.args, {"x": 3})
        self.assertEqual(ctx.origin, TurnOrigin.HUMAN)
        self.assertEqual(ctx.risk.risk_class, RISK_READ)
        self.assertGreater(ctx.started, 0.0)
        self.assertEqual(len(hook.after_calls), 1)
        self.assertIs(hook.after_calls[0][0], ctx, "同一调用的 ctx 贯穿前后")
        self.assertEqual(hook.after_calls[0][1], 6)
        self.assertEqual(hook.error_calls, [])

    def test_origin_defaults_unattended(self):
        """来源缺省按未声明（fail-closed），与门判定同一口径"""
        hook = RecordingHook()
        gated = wrap_tool(_calc_like(lambda x: x), thread_id="hook_test",
                          hooks=(hook,))
        gated.invoke({"x": 1})
        self.assertEqual(hook.before_calls[0].origin, TurnOrigin.UNATTENDED)

    def test_no_hooks_passthrough_unchanged(self):
        """hooks=()（缺省）直通：结果与无机制时代一致"""
        gated = wrap_tool(_calc_like(lambda x: x + 1), thread_id="hook_test")
        self.assertEqual(gated.invoke({"x": 41}), 42)

    def test_error_observed_then_propagates(self):
        """工具体异常：on_error 观察后原样抛出——hooks 无吞错权"""
        hook = RecordingHook()

        def boom(x):
            raise ValueError("boom")

        gated = wrap_tool(_calc_like(boom), thread_id="hook_test", hooks=(hook,))
        with self.assertRaises(ValueError):
            gated.invoke({"x": 1})
        self.assertEqual(len(hook.error_calls), 1)
        self.assertEqual(hook.error_calls[0][1].args, ("boom",))
        self.assertEqual(hook.after_calls, [], "失败调用不进 after")

    def test_gate_rejection_runs_before_not_after(self):
        """门拒绝的调用：before 观察到尝试，after 不触发（工具未执行）"""
        hook = RecordingHook()
        gated = wrap_tool(
            _calc_like(lambda x: x, name="brand_new_thing"),
            thread_id="hook_test", hooks=(hook,))
        result = gated.invoke({"x": 1})
        self.assertIn("审批门拒绝", result)
        self.assertEqual(len(hook.before_calls), 1)
        self.assertEqual(hook.after_calls, [])
        self.assertEqual(hook.error_calls, [], "门拒绝不是错误，不进 on_error")

    def test_async_path_runs_hooks_too(self):
        """异步执行面同机制：before/after 依序触发"""
        hook = RecordingHook()
        gated = wrap_tool(_calc_like(lambda x: x * 2), thread_id="hook_test",
                          hooks=(hook,))

        async def run():
            return await gated.ainvoke({"x": 5})

        self.assertEqual(asyncio.run(run()), 10)
        self.assertEqual(len(hook.before_calls), 1)
        self.assertEqual(hook.after_calls[0][1], 10)

    def test_wrap_all_tools_passes_hooks_through(self):
        """装配点把 hooks 传到每个包装件（装配单点接线）"""
        hook = RecordingHook()
        gated = wrap_all_tools([_calc_like(lambda x: x)], thread_id="hook_test",
                               hooks=(hook,))
        gated[0].invoke({"x": 1})
        self.assertEqual(len(hook.before_calls), 1)


class TestDomainDeniedReceipt(unittest.TestCase):
    """DomainDenied：wrapper 统一格式落拒绝回执，与手写三件套时代逐字一致。"""

    def _denied_tool(self, action="读取"):
        def body(x):
            require_domain("evil.example.com", tool_name="calculator",
                           action=action)
            return "不该到达"

        return _calc_like(body)

    def test_denial_returns_verbatim_reply_and_receipt(self):
        """拒绝话术与审计回执逐字一致；工具体的后续逻辑不触达"""
        gated = wrap_tool(self._denied_tool(), thread_id="hook_test")
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            result = gated.invoke({"x": 1})
        self.assertEqual(result, (
            "❌ 读取失败：读取请求被拒绝——目标域名 'evil.example.com' 不在允许名单内，"
            "本次读取已被域名白名单拦截并记录审计。"
        ))
        (receipt,) = mock_logger.log_event.call_args_list
        self.assertEqual(receipt.kwargs, {
            "thread_id": "system",
            "event": "system_action",
            "content": (
                "域名白名单拦截：工具 calculator 目标域 'evil.example.com' "
                "不在允许名单内，读取被拒绝。"
                "如需扩展，请设置 AUDITRONCLAW_ALLOWED_DOMAINS 环境变量。"
            ),
        })

    def test_denial_is_outcome_not_error_for_hooks(self):
        """DomainDenied 被转成拒绝结果：不进 on_error，也不进 after"""
        hook = RecordingHook()
        gated = wrap_tool(self._denied_tool(action="推送"), thread_id="hook_test",
                          hooks=(hook,))
        result = gated.invoke({"x": 1})
        self.assertIn("推送失败", result)
        self.assertEqual(hook.error_calls, [])
        self.assertEqual(hook.after_calls, [])

    def test_denied_exception_carries_denial_facts(self):
        """类型化异常携带拒绝三要素：工具名/目标域/动作（回执单源的原料）"""
        with self.assertRaises(DomainDenied) as caught:
            require_domain("evil.example.com", tool_name="t", action="读取")
        denied = caught.exception
        self.assertEqual((denied.tool_name, denied.domain, denied.action),
                         ("t", "evil.example.com", "读取"))


class TestAuditReceiptHook(unittest.TestCase):
    """AuditReceiptHook：成功回执单源——Receipt 返回值统一落 system 级事件。"""

    def test_receipt_return_logged_once_with_envelope(self):
        """工具返回 Receipt，hook 落 system/system_action 信封，内容逐字"""
        gated = wrap_tool(
            _calc_like(lambda x: Receipt("结果正文", "回执内容")),
            thread_id="hook_test", hooks=(AuditReceiptHook(),))
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            result = gated.invoke({"x": 1})
        self.assertEqual(result, "结果正文")
        self.assertIs(type(result), str, "回执取出后还原为普通 str")
        (event,) = mock_logger.log_event.call_args_list
        self.assertEqual(event.kwargs, {
            "thread_id": "system", "event": "system_action",
            "content": "回执内容",
        })

    def test_plain_str_result_no_event(self):
        """普通返回零事件（回执是工具自愿搭载的，不是机制强加的）"""
        gated = wrap_tool(_calc_like(lambda x: f"plain{x}"), thread_id="hook_test",
                          hooks=(AuditReceiptHook(),))
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            gated.invoke({"x": 1})
        mock_logger.log_event.assert_not_called()

    def test_raw_invoke_returns_receipt_behaving_as_str(self):
        """裸调用（无 wrapper）：Receipt 行为等同 str（比较/包含皆如常），
        回执无人落盘——裸工具不自带回执落盘，这正是回执单源的边界"""
        raw = _calc_like(lambda x: Receipt("正文", "回执"))
        result = raw.invoke({"x": 1})
        self.assertEqual(result, "正文")
        self.assertIn("正", result)
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            raw.invoke({"x": 2})
        mock_logger.log_event.assert_not_called()

    def test_receipt_travels_with_call_zero_shared_state(self):
        """并行两次调用（一载回执一不载）各自归账：回执随返回值走，
        不存在可被并行窗口误清/误取的共享暂存"""
        gated = wrap_tool(
            _calc_like(lambda x: Receipt("载回执", "回执内容") if x else "不载"),
            thread_id="hook_test", hooks=(AuditReceiptHook(),))
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            first = gated.invoke({"x": 1})
            second = gated.invoke({"x": 0})
        self.assertEqual(first, "载回执")
        self.assertEqual(second, "不载")
        logged = [c.kwargs.get("content")
                  for c in mock_logger.log_event.call_args_list]
        self.assertEqual(logged, ["回执内容"])


class TestPilotAcceptance(unittest.TestCase):
    """03 票验收钉子：试点工具体内无手写 log_event 回执。"""

    def test_pilot_mail_tool_has_no_handwritten_receipts(self):
        """mail_tool 全文不含 log_event：成功/失败回执由返回值携带 Receipt，
        拒绝回执由 wrapper 统一落——回执单源的 grep 验收写成测试，防回潮"""
        from auditronclaw.core.tools import mail_tool
        self.assertNotIn(
            "log_event", inspect.getsource(mail_tool),
            "试点工具不得手写 log_event 回执（回执单源在 hooks/wrapper）")


if __name__ == '__main__':
    unittest.main()
