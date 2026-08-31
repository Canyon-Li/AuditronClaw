import unittest
from unittest.mock import patch
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from helpers import FakeSender, InjectedSender, production_builtin_tools

from auditronclaw.core.tools import domain_gate
from auditronclaw.core.tools.domain_gate import (
    DEFAULT_ALLOWED_DOMAINS,
    DomainDenied,
    check_domain_allowed,
)
from auditronclaw.domains.feishu import tool as feishu_tool
from auditronclaw.domains.feishu.tool import send_feishu_summary


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
        with patch("auditronclaw.core.logger._audit_logger"):
            with patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com,example.org"}):
                domain_gate.refresh_extended_domains()
                self.assertTrue(check_domain_allowed("api.github.com"))
                self.assertTrue(check_domain_allowed("example.org"))
                # 扩展不清空默认名单
                for domain in DEFAULT_ALLOWED_DOMAINS:
                    self.assertTrue(check_domain_allowed(domain))

    def test_env_missing_falls_back_to_default(self):
        """扩展缺失回退默认名单"""
        with patch("auditronclaw.core.logger._audit_logger"):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUDITRONCLAW_ALLOWED_DOMAINS", None)
                domain_gate.refresh_extended_domains()
                self.assertFalse(check_domain_allowed("api.github.com"))


class TestDomainGateImportSafety(unittest.TestCase):
    """import 期不读 env（F2）：域名扩展的预热归装配期，不归模块加载期。

    历史：import 期读 AUDITRONCLAW_ALLOWED_DOMAINS、非空即落审计回执——
    变量一旦进进程环境（shell export 过），任何 import 本模块的入口都在
    logger 初始化前 RuntimeError。预热挪进 init_domain_gate（装配期
    logger 已锚定），模块级只留惰性默认。
    """

    def test_import_with_env_preset_does_not_crash(self):
        """子进程预置非空扩展变量后 import 链不炸（回归钉子）。"""
        import subprocess
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        code = "import auditronclaw.core.tools.domain_gate"
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            env={**os.environ, "AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com"},
            capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"import 期读 env 应已挪出模块加载期。stderr: {result.stderr}",
        )

    def test_init_prewarms_env_extension(self):
        """装配期行为不变：init 预热 env 扩展——审计恰一次、名单进缓存可放行"""
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            with patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com"}):
                domain_gate._LAST_ENV_RAW = None
                domain_gate._EXTENDED_DOMAINS = set()
                try:
                    domain_gate.init_domain_gate("unused_rules.json")
                    self.assertEqual(domain_gate._EXTENDED_DOMAINS, {"api.github.com"})
                    # 预热后判定直接命中缓存（不依赖 refresh 兜底）
                    self.assertTrue(check_domain_allowed("api.github.com"))
                    mock_logger.log_event.assert_called_once()
                    _, kwargs = mock_logger.log_event.call_args
                    self.assertEqual(kwargs.get("event"), "system_action")
                    self.assertIn("api.github.com", kwargs.get("content", ""))
                finally:
                    domain_gate._LAST_ENV_RAW = None
                    domain_gate._EXTENDED_DOMAINS = set()


class TestDomainGateAudit(unittest.TestCase):
    """守卫的审计事件形态（与 shell 白名单扩展同构）。"""

    def test_env_extension_logged(self):
        """扩展生效记审计：消息形态与命令白名单扩展一致"""
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            with patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "api.github.com"}):
                extended = domain_gate.load_extended_domains()
                self.assertEqual(extended, {"api.github.com"})
                mock_logger.log_event.assert_called_once()
                _, kwargs = mock_logger.log_event.call_args
                self.assertEqual(kwargs.get("thread_id"), "system")
                self.assertEqual(kwargs.get("event"), "system_action")
                self.assertIn("api.github.com", kwargs.get("content", ""))

    def test_denied_domain_logs_audit_event(self):
        """名单外域名拒绝时落审计事件：thread_id=system 级，含被拒域名与工具名
        （03 票起工具体抛 DomainDenied，由审批门 wrapper 统一格式落拒绝回执。
        经门调用以规则放行形态抵达工具体——名单外时分级是 domain_extend，
        守卫是门放行后的结构性兜底，先例 mail 试点 test_domain_guard_blocks）"""
        from auditronclaw.core.approval.gate import wrap_tool
        gated = wrap_tool(send_feishu_summary, thread_id="denial_probe",
                          rule_matcher=lambda *a, **k: {"id": "probe"})
        with patch("auditronclaw.core.logger._audit_logger") as mock_logger:
            with patch("auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
                       return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"):
                # 把飞书域从名单里挤出去：默认/环境变量/运行时审批规则三个
                # 名单来源全空（05 票起审批规则也是名单源，需一并隔离）
                with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
                     patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
                     patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
                    result = gated.invoke({"summary_text": "test"})
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
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
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
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
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
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
            return_value=secret,
        ):
            result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("失败", result)
        self.assertNotIn("SECRET_TOKEN", result)
        self.assertNotIn(secret, result)

    def test_missing_webhook_config_structured_error(self):
        """未配置 webhook：结构化错误，不碰网络"""
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
            return_value=None,
        ):
            result = send_feishu_summary.invoke({"summary_text": "test"})
        self.assertIn("失败", result)

    def test_raw_denial_raises_typed_exception(self):
        """裸调用（无 wrapper）在名单外直接抛 DomainDenied：拒绝回执的落盘
        与话术属 wrapper（回执单源），裸调方必须过门——03 票结构性变化"""
        fake = FakeSender()
        with InjectedSender(fake), patch(
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
            return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN",
        ):
            with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
                 patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
                 patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
                with self.assertRaises(DomainDenied):
                    send_feishu_summary.invoke({"summary_text": "test"})
        self.assertEqual(fake.sent, [], "守卫拦截后不得触达传输层")

    def test_tool_never_leaks_webhook_url(self):
        """凭据纪律：参数、返回值、审计日志全文不含 webhook URL 串"""
        secret = "https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.core.logger._audit_logger"
        ) as mock_logger:
            with patch("auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
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
        """工具参数 schema 不含 URL/sender 暴露面（sender 是模块内注入点，不对 LLM 开放）"""
        schema = send_feishu_summary.args_schema.model_json_schema()
        props = schema.get("properties", {})
        self.assertIn("summary_text", props)
        forbidden = {"url", "webhook", "webhook_url", "sender"}
        self.assertEqual(set(props) & forbidden, set())

    def test_tool_registered_in_builtins(self):
        """工具注册进内置工具清单（03 票起经装配接线传入——走生产同款装配采样）"""
        import tempfile
        from auditronclaw.core.config import WorkspaceConfig
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceConfig.from_root(tmp)
            names = {t.name for t in production_builtin_tools(workspace, "shape_probe")}
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
    03 票起回执由 wrapper 的 AuditReceiptHook 落盘——必须经门调用，
    回执才真实走一遍"工具 → hook → jsonl"全链，裸调用不写回执。
    注意不能对单例调 shutdown()（会杀全局工作线程，atexit 再 join 永久
    挂起）——用 log_queue.join() 等队列排空即可。
    """

    def test_audit_file_clean_after_tool_run(self):
        import uuid
        from auditronclaw.core.approval.gate import wrap_tool
        from auditronclaw.core.approval.hooks import AuditReceiptHook
        from auditronclaw.core.logger import get_audit_logger

        secret = f"https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN_{uuid.uuid4().hex[:12]}"
        gated = wrap_tool(send_feishu_summary, thread_id="gate_test",
                          hooks=(AuditReceiptHook(),))

        # 成功 + 异常消息内嵌 URL 的失败，两轮都走真实单例 logger（经门）
        with InjectedSender(FakeSender()), patch(
            "auditronclaw.domains.feishu.tool.get_feishu_webhook_url",
            return_value=secret,
        ):
            gated.invoke({"summary_text": "日报内容"})
            feishu_tool.set_sender(FakeSender(error=OSError(f"failed for {secret}")))
            gated.invoke({"summary_text": "日报内容"})

        # 等异步队列 flush 到 jsonl，再扫 system 级日志全文
        get_audit_logger().log_queue.join()
        system_log = os.path.join(get_audit_logger().log_dir, "system.jsonl")
        self.assertTrue(os.path.exists(system_log), "system 级审计日志应存在")
        with open(system_log, encoding="utf-8") as f:
            full_text = f.read()
        # 两轮回执都真实落盘了（成功 + 错误兜底），钉子不是形同虚设
        self.assertIn("飞书推送回执", full_text)
        self.assertIn("飞书推送失败", full_text)
        self.assertNotIn(secret, full_text)


if __name__ == '__main__':
    unittest.main()
