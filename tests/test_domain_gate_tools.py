import unittest
from unittest.mock import patch
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from helpers import FakeSender, InjectedSender

from auditronclaw.core.tools import domain_gate, feishu_tool
from auditronclaw.core.tools.domain_gate import (
    DEFAULT_ALLOWED_DOMAINS,
    check_domain_allowed,
)
from auditronclaw.core.tools.feishu_tool import send_feishu_summary


class TestDomainGatePureFunction(unittest.TestCase):
    """白名单守卫纯函数（网络实名门第一落地，同构 shell 命令白名单）。"""

    def test_default_domain_allowed(self):
        """名单内放行：默认名单里的域名直接通过"""
        for domain in DEFAULT_ALLOWED_DOMAINS:
            with self.subTest(domain=domain):
                self.assertTrue(check_domain_allowed(domain))

    def test_unknown_domain_denied(self):
        """名单外拒绝：任意第三方域名不放行"""
        self.assertFalse(check_domain_allowed("evil.example.com"))
        self.assertFalse(check_domain_allowed("open.feishu.cn.evil.com"))

    def test_env_extension_allowed(self):
        """环境变量扩展生效：AUDITRONCLAW_ALLOWED_DOMAINS 追加的域名放行"""
        with patch("auditronclaw.core.tools.domain_gate.audit_logger"):
            with patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com,example.org"}):
                domain_gate.refresh_extended_domains()
                self.assertTrue(check_domain_allowed("api.github.com"))
                self.assertTrue(check_domain_allowed("example.org"))
                # 扩展不清空默认名单
                for domain in DEFAULT_ALLOWED_DOMAINS:
                    self.assertTrue(check_domain_allowed(domain))

    def test_env_missing_falls_back_to_default(self):
        """扩展缺失回退默认名单"""
        with patch("auditronclaw.core.tools.domain_gate.audit_logger"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUDITRONCLAW_ALLOWED_DOMAINS", None)
                domain_gate.refresh_extended_domains()
                self.assertFalse(check_domain_allowed("api.github.com"))


class TestDomainGateAudit(unittest.TestCase):
    """守卫的审计事件形态（与 shell 白名单扩展同构）。"""

    def test_env_extension_logged(self):
        """扩展生效记审计：消息形态与命令白名单扩展一致"""
        with patch("auditronclaw.core.tools.domain_gate.audit_logger") as mock_logger:
            with patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com"}):
                extended = domain_gate.load_extended_domains()
                self.assertEqual(extended, {"api.github.com"})
                mock_logger.log_event.assert_called_once()
                _, kwargs = mock_logger.log_event.call_args
                self.assertEqual(kwargs.get("thread_id"), "system")
                self.assertEqual(kwargs.get("event"), "system_action")
                self.assertIn("api.github.com", kwargs.get("content", ""))

    def test_denied_domain_logs_audit_event(self):
        """名单外域名拒绝时落审计事件：thread_id=system 级，含被拒域名与工具名"""
        with patch("auditronclaw.core.tools.feishu_tool.audit_logger") as mock_logger:
            with patch("auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
                       return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"):
                # 把飞书域从名单里挤出去：默认/环境变量/运行时审批规则三个
                # 名单来源全空（05 票起审批规则也是名单源，需一并隔离）
                with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
                     patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
                     patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
                    result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("白名单拦截", result)
        denied_logged = False
        for call in mock_logger.log_event.call_args_list:
            kwargs = call.kwargs
            if "域名白名单拦截" in kwargs.get("content", ""):
                denied_logged = True
                self.assertEqual(kwargs.get("thread_id"), "system")
                self.assertIn("open.feishu.cn", kwargs["content"])
                self.assertIn("send_feishu_summary", kwargs["content"])
        self.assertTrue(denied_logged, "名单外拒绝必须落 system 级审计事件")


class TestSendFeishuSummary(unittest.TestCase):
    """命名推送工具行为：守卫先行、假 sender 注入、凭据不可见。"""

    def test_success_sends_via_injected_sender(self):
        """成功路径：假 sender 捕获发送内容，返回脱敏回执"""
        fake = FakeSender()
        with InjectedSender(fake), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN",
        ):
            result = send_feishu_summary.invoke({"summary_text": "邮箱事务台日报"})
        self.assertEqual(len(fake.sent), 1)
        url, payload = fake.sent[0]
        self.assertIn("open.feishu.cn", url)
        self.assertIn("邮箱事务台日报", str(payload))
        # 回执脱敏：成功 + 不含 webhook URL
        self.assertIn("成功", result)
        self.assertNotIn("SECRET_TOKEN", result)

    def test_failure_returns_structured_error(self):
        """失败路径：不抛裸异常，返回结构化错误（错误类型级，不透传异常消息）"""
        fake = FakeSender(error=ConnectionError("connection refused"))
        with InjectedSender(fake), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN",
        ):
            result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("失败", result)
        self.assertIn("ConnectionError", result)
        self.assertNotIn("SECRET_TOKEN", result)

    def test_exception_message_with_url_never_leaks(self):
        """凭据钉子：异常消息内嵌完整 URL 时，返回值与审计日志都不许泄露"""
        secret = "https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"
        # 逼真形态：urllib 异常消息常内嵌完整请求 URL
        fake = FakeSender(error=OSError(f"HTTP request failed for {secret}"))
        with InjectedSender(fake), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=secret,
        ):
            result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("失败", result)
        self.assertNotIn("SECRET_TOKEN", result)
        self.assertNotIn(secret, result)

    def test_missing_webhook_config_structured_error(self):
        """未配置 webhook：结构化错误，不碰网络"""
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=None,
        ):
            result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("失败", result)

    def test_tool_never_leaks_webhook_url(self):
        """凭据纪律：参数、返回值、审计日志全文不含 webhook URL 串"""
        secret = "https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.core.tools.feishu_tool.audit_logger"
        ) as mock_logger:
            with patch("auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
                       return_value=secret):
                # 成功一轮
                ok_result = send_feishu_summary.invoke({"summary_text": "日报内容"})
                # 失败一轮（注入抛 URL 进消息的异常）
                feishu_tool.set_sender(FakeSender(error=OSError(f"failed for {secret}")))
                fail_result = send_feishu_summary.invoke({"summary_text": "日报内容"})

        all_logged_text = ""
        for call in mock_logger.log_event.call_args_list:
            all_logged_text += str(call.args) + str(call.kwargs)

        self.assertIn("成功", ok_result)
        self.assertIn("失败", fail_result)
        self.assertNotIn("SECRET_TOKEN", ok_result)
        self.assertNotIn("SECRET_TOKEN", fail_result)
        self.assertNotIn("SECRET_TOKEN", all_logged_text)


class TestSendFeishuSummaryToolShape(unittest.TestCase):
    """工具形状：LLM 视角的参数面里没有 URL/凭据字段。"""

    def test_tool_args_have_no_url_field(self):
        """工具参数 schema 不含 URL/sender 暴露面（sender 是模块内注入缝，不对 LLM 开放）"""
        schema = send_feishu_summary.args_schema.model_json_schema()
        props = schema.get("properties", {})
        self.assertIn("summary_text", props)
        forbidden = {"url", "webhook", "webhook_url", "sender"}
        self.assertEqual(set(props) & forbidden, set())

    def test_tool_registered_in_builtins(self):
        """工具注册进内置工具清单"""
        from auditronclaw.core.tools.builtins import BUILTIN_TOOLS
        names = {t.name for t in BUILTIN_TOOLS}
        self.assertIn("send_feishu_summary", names)

    def test_docstring_documents_boundary(self):
        """docstring 写明推送边界与域名约束"""
        doc = send_feishu_summary.description
        self.assertIn("飞书", doc)
        self.assertIn("域名", doc)


class TestCredentialNeverReachesAuditFile(unittest.TestCase):
    """凭据纪律的落盘级验证：真实审计 jsonl 全文不含 webhook URL 串。

    与 mock 断言互补——logger 是单例 + 异步队列，必须等队列 flush 到
    文件后再扫全文，钉住"凭据不落任何一行日志"。
    注意不能对单例调 shutdown()（会杀全局工作线程，atexit 再 join 永久
    挂起）——用 log_queue.join() 等队列排空即可。
    """

    def test_audit_file_clean_after_tool_run(self):
        import uuid
        from auditronclaw.core.logger import audit_logger

        secret = f"https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN_{uuid.uuid4().hex[:12]}"

        # 成功 + 异常消息内嵌 URL 的失败，两轮都走真实单例 logger
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=secret,
        ):
            send_feishu_summary.invoke({"summary_text": "日报内容"})
            feishu_tool.set_sender(FakeSender(error=OSError(f"failed for {secret}")))
            send_feishu_summary.invoke({"summary_text": "日报内容"})

        # 等异步队列 flush 到 jsonl，再扫 system 级日志全文
        audit_logger.log_queue.join()
        system_log = os.path.join(audit_logger.log_dir, "system.jsonl")
        self.assertTrue(os.path.exists(system_log), "system 级审计日志应存在")
        with open(system_log, encoding="utf-8") as f:
            full_text = f.read()
        self.assertNotIn(secret, full_text)


if __name__ == '__main__':
    unittest.main()
