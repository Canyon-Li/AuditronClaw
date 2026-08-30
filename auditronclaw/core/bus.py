import asyncio
from dataclasses import dataclass

from .approval.gate import TurnOrigin

# 队列元素:类型化回合信封,外加裸控制令牌(如 "/exit",见 docstring 下文)
task_queue: "asyncio.Queue[TurnRequest | str]" = asyncio.Queue()


@dataclass(frozen=True)
class TurnRequest:
    """回合请求信封:队列项不再是裸字符串,来源随文本类型化同行。

    心跳塞 HEARTBEAT、终端用户输入塞 HUMAN——引擎按 origin 判定问人资格,
    文本前缀(【系统内部心跳触发】…)只是给模型看的提示内容,不再是来源
    标记(可被用户伪造,frozen 信封伪造不了)。控制令牌(/exit)保持裸串。
    """

    text: str
    origin: TurnOrigin
