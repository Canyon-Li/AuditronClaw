"""出站通道注册表：名 → (getter, setter)，每通道自带哨兵深度。

出站通道 = 命名网络工具的生产传输路径（feishu webhook POST、IMAP SSL 取信）。
零真实网络是测试套件的结构性纪律，哨兵深度是通道属性、随定义登记在条目
上：feishu 守注入点 _http_sender（它本身就是网络边界）；IMAP 守真套接字
边界 imaplib.IMAP4_SSL——不降级为只换注入点（那会浅一层：生产 provider
允许被测，mock 传输层走全流程的用例合法，守门只挡真实连接）。conftest
遍历本注册表布哨，不再手工枚举通道。

新增通道：在传输定义同文件 register_egress_channel（forcing function——
登记与定义同址）；漏登记由 meta-test 判红（tests/test_egress_registry.py：
import 网络库的模块必须在注册表有条目）。
"""
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class EgressChannel:
    """一条出站通道的守卫登记。

    getter/setter 定位哨兵换装点（生产传输件本体）；测试注入假件走各工具
    自己的注入点（set_provider/set_sender），与此处的哨兵换装是两层。
    """

    name: str                        # 通道名（触碰账与哨兵报错用它说话）
    module: str                      # 传输定义所在模块（meta-test 判"已登记"的依据）
    getter: Callable[[], Any]        # 取当前生产传输件（哨兵换装前的原值）
    setter: Callable[[Any], None]    # 换传输件（哨兵换装/还原）
    guard: str                       # 哨兵深度：守在哪层、为什么、测试该怎么注入


_EGRESS_CHANNELS: dict = {}


def register_egress_channel(channel: EgressChannel) -> None:
    _EGRESS_CHANNELS[channel.name] = channel


def egress_channels() -> tuple:
    """当前注册表的快照（conftest 布哨与 meta-test 判定都吃这份）。"""
    return tuple(_EGRESS_CHANNELS.values())
