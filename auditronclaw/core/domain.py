"""域包登记契约（域模板 ADR-002 的代码面）：一个域对外的全部自报。

类型放 core 是依赖方向决定的：domains → core 单向，域 import 本类型，
core 不 import 任何域。域作者只经 register() 返回本类型交付能力，
槽位与负空间规则见 docs/adr/ADR-002。

risk 只收静态分级词汇（级别不随运行时状态变的三个值）。条件分级工具
（级别依赖运行时状态，如绑定域工具随域名白名单当刻内容分支）一律留在
core 名册、域不自报——frozen 声明式快照捕获不了运行时状态。
"""
from dataclasses import dataclass
from typing import Literal, Mapping

from langchain_core.tools import BaseTool

from .tools.egress import EgressChannel

# 静态分级词汇：与 approval/classifier.py 的 RISK_READ / RISK_WRITE /
# RISK_DELETE 同值（CONTEXT.md「副作用分级」里级别固定的三个）。execute /
# domain_extend 是运行时判定产物、unclassified 是未入册缺省，都不在自报面。
RiskCategory = Literal["read", "write", "delete"]


@dataclass(frozen=True)
class DomainRegistration:
    """一个域的装配期登记：工具、静态分级自报、出站通道声明。

    每域恰好一个 register() 返回本类型（约定落 ADR-002）；tools 为空
    仅限测试夹具域。frozen 与 WorkspaceConfig 同一风格——装配期快照，
    构造即固化，装配后不许再改。
    """

    tools: tuple[BaseTool, ...]
    risk: Mapping[str, RiskCategory]        # 仅静态分级词汇；条件分级工具不在此声明
    egress: tuple[EgressChannel, ...] = ()  # 无出站留空
