"""域名一致性 meta-test（ADR-002 裁定 3 的强制面，03 票）。

core 绑定域册的域名以字符串字面量写死——core 不 import 域常量（依赖方向
domains → core 单向）。字面量与域侧声明的一致性在此把守：feishu 已迁
域包，字面量 == register().egress 声明的域名 == 域内守卫实际使用的常量；
mail 未迁（存量在 core），字面量 == mail_tool.IMAP_DOMAIN——同一条
规则，两种声明面。漂移（改名、打错字）在这里红，而不是在心跳回合里
静默判错级别。
"""
import unittest

from auditronclaw.core.approval.classifier import _BOUND_DOMAIN_TOOLS
from auditronclaw.core.tools.mail_tool import IMAP_DOMAIN
from auditronclaw.domains.feishu import tool as feishu_domain


class TestBoundDomainLiterals(unittest.TestCase):
    """core 册字面量 == 域侧声明：两处事实源永不漂移。"""

    def test_feishu_literal_equals_domain_declaration(self):
        """feishu：字面量 == register().egress 声明域名 == 域内守卫常量"""
        registration = feishu_domain.register()
        declared = {ch.domain for ch in registration.egress}
        self.assertEqual(declared, {feishu_domain.FEISHU_WEBHOOK_DOMAIN},
                         "register().egress 必须声明域内守卫实际使用的域名")
        self.assertEqual(_BOUND_DOMAIN_TOOLS["send_feishu_summary"],
                         feishu_domain.FEISHU_WEBHOOK_DOMAIN,
                         "core 册字面量与 feishu 域声明漂移")

    def test_feishu_literal_serves_the_registered_tool(self):
        """字面量服务的工具确属域自报（条件分级：工具本体在域、分级在 core）"""
        registration = feishu_domain.register()
        self.assertIn("send_feishu_summary", {t.name for t in registration.tools})

    def test_mail_literal_equals_core_constant(self):
        """mail（未迁域，存量在 core）：字面量 == mail_tool.IMAP_DOMAIN"""
        self.assertEqual(_BOUND_DOMAIN_TOOLS["read_recent_emails"], IMAP_DOMAIN,
                         "core 册字面量与 mail_tool.IMAP_DOMAIN 漂移")


if __name__ == "__main__":
    unittest.main()
