"""工具调用 hooks：审批门 wrapper 的观察缝（03 票 C）。

安全语义长在包装单点：DomainDenied 的拒绝回执由 gate.wrap_tool 统一落
（格式单源在 tools/domain_gate.py），成功回执由本模块的 AuditReceiptHook
单源落。遥测（耗时/成败）不在 wrapper——定案走 LangChain callbacks，
callbacks 只读无策略能力；hooks 只观察与记录，无否决权，否决权只属
审批门。体量上限：Protocol + for 循环，不引入框架。

生命周期：before 见每次经门调用的尝试（含被门拒绝的）；after 只见工具体
执行后的结果；on_error 只见工具体异常（DomainDenied 被 wrapper 转成拒绝
结果，不算错误）。interrupt 问人续行会让工具节点整批重跑，before 随之
重入——hooks 不得假设 before/after 严格成对，回执随返回值走因此不受影响。
本模块不 import 分类器/门——它们反向引用本模块，类型注解走
TYPE_CHECKING，环不进运行期。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..logger import audit_logger

if TYPE_CHECKING:
    from .classifier import RiskAssessment
    from .gate import TurnOrigin


@dataclass(frozen=True)
class ToolCallContext:
    """一次工具调用的观察面（hooks 只读）。

    risk 是本次调用的副作用分级结果；origin 是回合来源（心跳/基准/未声明
    构造上永不问人，缺省按未声明 fail-closed）；started 为单调时钟起点。
    """

    tool: str
    args: dict
    origin: "TurnOrigin"
    risk: "RiskAssessment | None"
    started: float


class ToolHook(Protocol):
    """hook 契约：三个观察位，默认实现是直通（after 原样返回、on_error 原样抛）。"""

    def before(self, ctx: ToolCallContext) -> None: ...

    def after(self, ctx: ToolCallContext, result):
        return result

    def on_error(self, ctx: ToolCallContext, exc: Exception):
        raise exc


class Receipt(str):
    """带审计回执的返回值（03 票）：str 子类，正文原样、回执随值走。

    为什么走返回值：工具体与 wrapper 之间隔着 BaseTool 的上下文拷贝边界
    （invoke/ainvoke 都在 copied context 里跑工具体，实测 set 跨不回去），
    contextvar/模块级暂存要么过不了界、要么并行调用下回执误归账；返回值
    天然随本次调用走，零共享状态。裸调用（无 wrapper）拿到的是行为完全
    等同 str 的值——回执无人落盘，也不污染返回值。
    """

    __slots__ = ("audit_content",)

    def __new__(cls, content: str, audit_content: str):
        self = super().__new__(cls, content)
        self.audit_content = audit_content
        return self


class AuditReceiptHook(ToolHook):
    """成功回执单源：Receipt 返回值统一取出，落 system 级审计事件。

    工具体内 return Receipt(给LLM看的正文, 审计回执文案)，落盘只在
    这里一处——工具体不再手写 log_event 回执。
    """

    def before(self, ctx: ToolCallContext) -> None:
        pass  # 回执随返回值走，无前置状态可清

    def after(self, ctx: ToolCallContext, result):
        if type(result) is Receipt:
            audit_logger.log_event(
                thread_id="system", event="system_action",
                content=result.audit_content)
            return str(result)  # 还原普通 str：回执取出后不留子类痕迹
        return result

    def on_error(self, ctx: ToolCallContext, exc: Exception):
        raise exc
