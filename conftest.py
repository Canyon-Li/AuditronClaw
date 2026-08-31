"""测试进程级生产通道哨兵（03 票起遍历出站通道注册表）。

放在仓库根而非 tests/ 下（域模板 ADR-002）：pytest 全量收集 tests/ 与
auditronclaw/domains/（testpaths 钉在 pytest.ini），根级 conftest 对两处
测试同样生效——域目录测试同受出站哨兵保护，不用每处复制布哨。

零真实网络是测试套件的结构性纪律：任何测试真实触碰出站通道，当条测试
即红——不赌写测试的人每次都记得注入假件。发现路径：2026-08-24
test_bench_pipeline 的"退出探针"在本地 .env 存在时（bench_pipeline 导入即
load_dotenv），把 7 字符探针文本真实推到作者飞书群 60 次（system.jsonl 回执
为证）——工具层的结构化错误兜底会吞掉探针异常，所以违规必须落账、由本夹具
收尾断言，不能指望异常自己传出工具层。

守卫层级随通道定义登记（每通道自带哨兵深度，见 auditronclaw/core/tools/
egress.py）：feishu 守注入点 _http_sender（它本身就是网络边界）；IMAP 守真
套接字边界 imaplib.IMAP4_SSL（生产 provider 允许被测，守门只挡真实连接）。
新增通道在传输定义同文件登记，漏登记由 meta-test 判红
（test_egress_registry）。触碰账只记通道名——参数里可能有凭据（webhook
URL 带签名 token、IMAP 登录带授权码），不进账面。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入即登记：注册表条目挂在传输定义模块上（feishu 03 票起在域包）
from auditronclaw.core.tools import mail_tool  # noqa: F401
from auditronclaw.domains.feishu import tool as feishu_tool  # noqa: F401
from auditronclaw.core.tools.egress import egress_channels
from auditronclaw.core.logger import init_audit_logger


@pytest.fixture(autouse=True, scope="session")
def session_audit_logger(tmp_path_factory):
    """会话级审计锚(05 票):测试进程的审计统一落临时目录。

    审计 logger 是装配期对象(入口 init_audit_logger 构造一次),测试进程
    没有入口——本夹具即是入口:不初始化则任何走审计的代码路径
    get_audit_logger() 即拒;初始化到仓库 workspace/logs 则测试写脏生产
    落点。需要截获事件的测试 patch auditronclaw.core.logger._audit_logger。
    """
    init_audit_logger(str(tmp_path_factory.mktemp("audit_logs")))

# (通道名) 触碰账。
_TOUCHES = []


class ProductionChannelTouched(AssertionError):
    """出站通道的生产传输层在测试进程中被调用。"""


def _sentinel(channel):
    def touch(*args, **kwargs):
        _TOUCHES.append(channel.name)
        raise ProductionChannelTouched(
            f"测试进程不允许真实出站通道 {channel.name}；{channel.guard}")
    return touch


@pytest.fixture(autouse=True)
def production_channel_guard():
    """全测试生效：注册表内每条通道的网络边界换哨兵，收尾断言零触碰（含被工具层吞掉的）。"""
    channels = egress_channels()
    originals = {ch.name: ch.getter() for ch in channels}
    for ch in channels:
        ch.setter(_sentinel(ch))
    _TOUCHES.clear()
    try:
        yield
    finally:
        for ch in channels:
            ch.setter(originals[ch.name])
        assert not _TOUCHES, f"出站通道被真实触碰: {sorted(set(_TOUCHES))}"
