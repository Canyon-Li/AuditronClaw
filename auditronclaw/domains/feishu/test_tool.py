"""feishu 域包验收钉子（域模板 ADR-002 首个实测者，03 票）。

实测四方面（见 ADR-002 分工×首个实测者表）：tool.py 形状、回执全走
Receipt→hooks、拒绝话术单源（域内零拷贝）、egress 声明随传输定义同文件。
risk 为空是设计结果：send_feishu_summary 是绑定域工具（条件分级），按
ADR-002 裁定留 core 名册、域不自报——与合并册的互斥性由票 02 meta-test
永久把守；域名一致性由 tests/test_domain_name_consistency.py 把守。
"""
import os
import unittest

from auditronclaw.core.domain import DomainRegistration
from auditronclaw.core.tools import egress
from auditronclaw.domains.feishu import tool

_DOMAIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _domain_sources():
    """域包全部源文件 (文件名, 源码)：钉子扫整个域包，新增文件同受纪律。"""
    for fn in sorted(os.listdir(_DOMAIN_DIR)):
        if fn.endswith(".py") and fn != os.path.basename(__file__):
            with open(os.path.join(_DOMAIN_DIR, fn), encoding="utf-8") as f:
                yield fn, f.read()


class TestDomainDiscipline(unittest.TestCase):
    """域内纪律（mail 试点同款 grep 钉子，防回潮）。"""

    def test_no_handwritten_log_event(self):
        """域内零手写 log_event：回执单源在 Receipt→hooks 与 DomainDenied→wrapper"""
        for fn, src in _domain_sources():
            with self.subTest(file=fn):
                self.assertNotIn(
                    "log_event", src,
                    "域内不得手写 log_event 回执（回执单源：Receipt→hooks / 拒绝→wrapper）")

    def test_no_local_denial_wording_copy(self):
        """拒绝话术全库单源：域内不得出现第二份拷贝（单源在 core 的 domain_gate）"""
        for fn, src in _domain_sources():
            with self.subTest(file=fn):
                self.assertNotIn(
                    "不在允许名单内", src,
                    "拒绝话术拷贝回流：单源在 domain_gate"
                    "（domain_denied_reply / domain_denied_audit_content）")


class TestRegisterContract(unittest.TestCase):
    """register() 契约（ADR-002 分工表的接线槽位）。"""

    def test_register_returns_domain_registration_with_tool(self):
        """每域恰好一个 register()：返回 DomainRegistration，携本域工具"""
        reg = tool.register()
        self.assertIsInstance(reg, DomainRegistration)
        self.assertEqual([t.name for t in reg.tools], ["send_feishu_summary"])

    def test_risk_empty_by_design(self):
        """risk 为空是设计结果：绑定域工具（条件分级）不自报，留 core 名册"""
        reg = tool.register()
        self.assertEqual(dict(reg.risk), {})
        self.assertNotIn("send_feishu_summary", reg.risk,
                         "绑定域工具名不得出现在 risk 映射（ADR-002 负空间）")

    def test_egress_declared_with_transport_and_domain(self):
        """egress 声明随传输定义同文件、带绑定域名、引用已登记的同一条通道"""
        reg = tool.register()
        self.assertEqual(len(reg.egress), 1)
        channel = reg.egress[0]
        self.assertEqual(channel.module, tool.__name__,
                         "登记与传输定义同址（module 指向本模块）")
        self.assertEqual(channel.domain, tool.FEISHU_WEBHOOK_DOMAIN,
                         "register().egress 必须声明本域守卫实际使用的域名")
        registered = {ch.name: ch for ch in egress.egress_channels()}
        self.assertIs(registered[channel.name], channel,
                      "register().egress 引用的必须是模块级登记的同一条通道")

    def test_bound_domain_in_default_allowlist(self):
        """绑定域在默认名单内：生产通道可用（名单是单一事实源）"""
        from auditronclaw.core.tools.domain_gate import DEFAULT_ALLOWED_DOMAINS
        self.assertIn(tool.FEISHU_WEBHOOK_DOMAIN, DEFAULT_ALLOWED_DOMAINS)


if __name__ == "__main__":
    unittest.main()
