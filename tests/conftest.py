"""测试进程级生产通道哨兵。

零真实网络是测试套件的结构性纪律：任何测试真实触碰飞书 POST 或 IMAP 连接，
当条测试即红——不赌写测试的人每次都记得注入假件。发现路径：2026-08-24
test_bench_pipeline 的"退出探针"在本地 .env 存在时（bench_pipeline 导入即
load_dotenv），把 7 字符探针文本真实推到作者飞书群 60 次（system.jsonl 回执
为证）——工具层的结构化错误兜底会吞掉探针异常，所以违规必须落账、由本夹具
收尾断言，不能指望异常自己传出工具层。

守卫层级：飞书守在 feishu_tool._http_sender（它本身就是网络边界，测试一律
经注入假 sender）；IMAP 守在 imaplib.IMAP4_SSL（真套接字边界）而非
mail_tool._imap_provider——生产 provider 允许被测（mock 传输层走全流程的
用例合法，见 TestImapReadsWithMockedTransport），守门只挡真实连接。
"""

import imaplib
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.tools import feishu_tool

# (通道, 说明) 触碰账。账面不得含凭据：webhook URL 带签名 token，IMAP 登录
# 带账号与授权码——只记通道名与脱敏概要。
_TOUCHES = []


class ProductionChannelTouched(AssertionError):
    """生产传输层在测试进程中被调用。"""


def _feishu_sentinel(webhook_url, payload):
    domain = str(webhook_url).split("/hook/")[0]
    _TOUCHES.append(("feishu_tool._http_sender", f"POST {domain}/…"))
    raise ProductionChannelTouched(
        "测试进程不允许真实飞书 POST；请用 helpers.InjectedSender 注入假 sender")


def _imap_ssl_sentinel(host, *args, **kwargs):
    _TOUCHES.append(("imaplib.IMAP4_SSL", f"connect {host}"))
    raise ProductionChannelTouched(
        "测试进程不允许真实 IMAP 连接；测生产 provider 请 mock imaplib.IMAP4_SSL 传输层")


@pytest.fixture(autouse=True)
def production_channel_guard():
    """全测试生效：网络边界换哨兵，收尾断言零触碰（含被工具层吞掉的）。"""
    originals = (feishu_tool._http_sender, imaplib.IMAP4_SSL)
    feishu_tool._http_sender = _feishu_sentinel
    imaplib.IMAP4_SSL = _imap_ssl_sentinel
    _TOUCHES.clear()
    try:
        yield
    finally:
        feishu_tool._http_sender, imaplib.IMAP4_SSL = originals
        assert not _TOUCHES, f"生产通道被真实触碰: {_TOUCHES}"
