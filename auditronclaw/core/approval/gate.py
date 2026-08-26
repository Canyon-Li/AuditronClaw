"""审批门(01 票无人形态):装配点统一包装的工具调用守卫。

词汇见 CONTEXT.md「审批门」:副作用分级判为高危的工具调用,必经既定规则
放行或人批准才执行,审批事件入审计。拦的是"受骗的合法操作"——写与执行
同门,依据是人批没批。

求值顺序固定:分级 → 规则 → 问人。分级结果是规则匹配的输入,规则只能
豁免必批级动作,改变不了分级本身。本票收在无人形态:无规则且无应答通道
→ 拒绝并继续(拒绝作为 tool_result 返回,agent 回合内自行收尾,不中止
不挂起)。规则系统与打断缝分别归 02/03 票,缝位在本模块:
- rule_matcher:02 票的规则匹配器接入位,命中即放行(source=rule_auto)
- 问人位:03 票以 LangGraph interrupt 落在规则判定之后
"""
from enum import Enum
from typing import Callable, FrozenSet, List, Optional

from langchain_core.tools import BaseTool, StructuredTool

from ..logger import audit_logger
from ..skill_loader import SKILL_FOLDER_META_KEY
from .classifier import (
    Provenance,
    RiskAssessment,
    classify_tool_call,
)

# ============ 审计事件(审批留痕是本章凭证主体,不塞 system_action) ============

EVENT_APPROVAL_REQUESTED = "approval_requested"
EVENT_APPROVAL_DECISION = "approval_decision"
EVENT_RULE_PERSISTED = "rule_persisted"


class DecisionSource(str, Enum):
    """审批决定的来源(与 03 票 ApprovalDecision.source 共用枚举)。"""

    RULE_AUTO = "rule_auto"        # 既定规则自动放行(02 票)
    USER_ONCE = "user_once"        # 人批准一次(03/04 票)
    USER_PERSIST = "user_persist"  # 人批准并"永久允许"铸规则(02/04 票)
    TIMEOUT = "timeout"            # 审批等待超时=拒绝(03 票)
    UNATTENDED = "unattended"      # 无人值守且无规则:拒绝并继续


# 门拒绝话术的标志词:落进基准 _REJECT_MARKERS 词表,否则被误判为落地
REJECT_PHRASE = "审批门拒绝"


def rejection_text(tool_name: str, assessment: RiskAssessment) -> str:
    """无人形态拒绝话术:带原因、点名具体动作,作为 tool_result 返回。"""
    return (
        f"❌ {REJECT_PHRASE}：工具 {tool_name} 的本次调用属于必批副作用"
        f"（{assessment.risk_class}：{assessment.reason}）。"
        "当前无人值守且无匹配审批规则，本次调用未执行。"
        "如属日常合法操作，请在有人交互时批准或铸审批规则后再试。"
    )


# 规则匹配器契约(02 票实现,此处只定签名):
# rule_matcher(tool_name, args, assessment) -> 命中的规则(dict)或 None
RuleMatcher = Callable[[str, dict, RiskAssessment], Optional[dict]]


def _log_approval_requested(thread_id: str, tool_name: str, args: dict,
                            assessment: RiskAssessment) -> None:
    audit_logger.log_event(
        thread_id=thread_id,
        event=EVENT_APPROVAL_REQUESTED,
        tool=tool_name,
        args=dict(args),
        risk_class=assessment.risk_class,
        reason=assessment.reason,
    )


def _log_approval_decision(thread_id: str, tool_name: str,
                           assessment: RiskAssessment, approved: bool,
                           source: DecisionSource) -> None:
    audit_logger.log_event(
        thread_id=thread_id,
        event=EVENT_APPROVAL_DECISION,
        tool=tool_name,
        approved=approved,
        source=source.value,
        risk_class=assessment.risk_class,
    )


def log_rule_persisted(thread_id: str, rule: dict) -> None:
    """规则铸成事件(02 票接线;形状此处定死:条目整体搭载)。"""
    audit_logger.log_event(
        thread_id=thread_id,
        event=EVENT_RULE_PERSISTED,
        rule=rule,
    )


def wrap_tool(
    tool: BaseTool,
    *,
    thread_id: str,
    provenance: Provenance = Provenance.BUILTIN,
    skill_folder: str = "",
    rule_matcher: Optional[RuleMatcher] = None,
) -> StructuredTool:
    """给单个工具包上门:同名同 schema,调用先过 分级→规则→(问人) 链。"""

    def _decide(kwargs: dict):
        """固定链主体。返回 (放行, 拒绝话术|None)。"""
        assessment = classify_tool_call(
            tool.name, kwargs, provenance=provenance, skill_folder=skill_folder)
        if not assessment.requires_approval:
            return True, None

        _log_approval_requested(thread_id, tool.name, kwargs, assessment)

        rule = rule_matcher(tool.name, kwargs, assessment) \
            if rule_matcher is not None else None
        if rule is not None:
            _log_approval_decision(thread_id, tool.name, assessment,
                                   approved=True, source=DecisionSource.RULE_AUTO)
            return True, None

        # 无人形态(01 票):无规则且无应答通道 → 拒绝并继续。
        # 03 票的 interrupt 问人位插在规则判定之后、此处拒绝之前。
        _log_approval_decision(thread_id, tool.name, assessment,
                               approved=False, source=DecisionSource.UNATTENDED)
        return False, rejection_text(tool.name, assessment)

    def gated_run(**kwargs):
        allowed, rejection = _decide(kwargs)
        if not allowed:
            return rejection
        return tool.invoke(kwargs)

    async def gated_arun(**kwargs):
        allowed, rejection = _decide(kwargs)
        if not allowed:
            return rejection
        return await tool.ainvoke(kwargs)

    return StructuredTool.from_function(
        func=gated_run,
        coroutine=gated_arun,
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        metadata={**(tool.metadata or {}), "approval_gate": True},
    )


def wrap_all_tools(
    tools: List[BaseTool],
    *,
    thread_id: str,
    extra_names: FrozenSet[str] = frozenset(),
    rule_matcher: Optional[RuleMatcher] = None,
) -> List[BaseTool]:
    """装配点统一包装:内置/技能/外接全部过门。

    来源判定:外接名单内 → extra(默认必批);带 skill_folder 元数据 →
    skill(按命令收敛);其余 → builtin(查副作用册)。
    不做"已包装"短路:包装标记写在工具元数据里,而元数据来自被守对象
    (外接工具可自带任意元数据),守门判定不能握在被守者手里。
    """
    wrapped = []
    for t in tools:
        folder = (t.metadata or {}).get(SKILL_FOLDER_META_KEY, "")
        if t.name in extra_names:
            provenance = Provenance.EXTRA
        elif folder:
            provenance = Provenance.SKILL
        else:
            provenance = Provenance.BUILTIN
        wrapped.append(wrap_tool(
            t, thread_id=thread_id, provenance=provenance,
            skill_folder=folder, rule_matcher=rule_matcher))
    return wrapped
