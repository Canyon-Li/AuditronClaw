"""域包登记契约单测：DomainRegistration 的 frozen 与字段（域模板 ADR-002）。

这是契约类型本身的外显行为测试（冻结、默认值、词汇），不是内部实现
细节——域作者依赖的全部语义在此钉住。合并册与同名冲突的装配期行为
是后续票的对象，此处不造 stub 域凑验证。
"""
import os
import sys
import unittest
from dataclasses import FrozenInstanceError
from typing import get_args

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from langchain_core.tools import tool

from auditronclaw.core.domain import DomainRegistration, RiskCategory
from auditronclaw.core.tools.egress import EgressChannel


@tool
def fixture_only_tool(text: str) -> str:
    """测试夹具工具（不属任何域，只证明 tools 槽收 BaseTool）"""
    return text


def _fake_sender(webhook_url, payload):
    return {"code": 0}


class TestDomainRegistrationContract(unittest.TestCase):
    """契约面：frozen、字段、默认值——域作者依赖的语义。"""

    def test_frozen_after_construction(self):
        """装配期快照：构造后改字段必红（与 WorkspaceConfig 同一风格）"""
        reg = DomainRegistration(tools=(fixture_only_tool,), risk={})
        with self.assertRaises(FrozenInstanceError):
            reg.tools = ()

    def test_fields_roundtrip(self):
        """三个槽位原样保存：tools / risk / egress"""
        egress = (EgressChannel(
            name="fixture_channel", module=__name__,
            getter=lambda: _fake_sender, setter=lambda t: None,
            guard="测试夹具条目，不注册进全局表"),)
        reg = DomainRegistration(
            tools=(fixture_only_tool,),
            risk={"fixture_only_tool": "write"},
            egress=egress)
        self.assertEqual(reg.tools, (fixture_only_tool,))
        self.assertEqual(reg.risk, {"fixture_only_tool": "write"})
        self.assertEqual(reg.egress, egress)

    def test_egress_defaults_to_empty(self):
        """无出站的域留空——egress 缺省是空元组不是 None"""
        reg = DomainRegistration(tools=(), risk={})
        self.assertEqual(reg.egress, ())

    def test_risk_vocabulary_is_static_only(self):
        """risk 词汇只收静态分级三值：read / write / delete。

        条件分级产物（execute / domain_extend）与未入册缺省
        （unclassified）不在自报面——条件分级工具留 core 名册。
        """
        self.assertEqual(get_args(RiskCategory), ("read", "write", "delete"))


if __name__ == '__main__':
    unittest.main()
