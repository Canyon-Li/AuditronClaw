"""审批门:装配点统一包装的工具调用守卫。

词汇见 CONTEXT.md「审批门」:副作用分级判为高危的工具调用,必经既定规则
放行或人批准才执行,审批事件入审计。拦的是"受骗的合法操作"——写与执行
同门,依据是人批没批。

求值顺序固定:分级 → 规则 → 问人。分级结果是规则匹配的输入,规则只能
豁免必批级动作,改变不了分级本身。问人是 LangGraph interrupt(03 票):
人来源回合规则未命中时打断图,应答 ApprovalDecision 后 Command resume
续行——批准与执行在同一份规范化调用里(门包住 invoke,TOCTOU 无窗)。
心跳/基准/缺省来源构造上不问人:直接拒(source=unattended),不挂起。

审批留痕与节点重跑:interrupt 续行会让工具节点整批重跑(纯读段重读无害,
必批调用重新走链)。requested/decision 因此都在决定落定时成对落盘——
问人时落盘 requested 会在续行重跑时双写;审计对的语义是"一次过审流程的
请求与决定"。规则命中的调用没有问过人,只有 decision(source=rule_auto)。
"""
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Callable, FrozenSet, List, Optional, Sequence

from langchain_core.runnables import ensure_config
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import interrupt

from ..logger import audit_logger
from ..skill_loader import SKILL_FOLDER_META_KEY
from ..tools.domain_gate import (
    DomainDenied,
    domain_denied_audit_content,
    domain_denied_reply,
)
from .classifier import (
    Provenance,
    RiskAssessment,
    classify_tool_call,
)
from .hooks import ToolCallContext, ToolHook

# ============ 审计事件(审批留痕是本章凭证主体,不塞 system_action) ============

EVENT_APPROVAL_REQUESTED = "approval_requested"
EVENT_APPROVAL_DECISION = "approval_decision"
EVENT_RULE_PERSISTED = "rule_persisted"
EVENT_RULE_REVOKED = "rule_revoked"  # 02 票:撤销留痕(管理面,与铸规则对称)


class DecisionSource(str, Enum):
    """审批决定的来源(与 ApprovalDecision.source 共用枚举)。"""

    RULE_AUTO = "rule_auto"        # 既定规则自动放行(02 票)
    USER_ONCE = "user_once"        # 人批准一次(03/04 票)
    USER_PERSIST = "user_persist"  # 人批准并"永久允许"铸规则(02/04 票)
    TIMEOUT = "timeout"            # 审批等待超时=拒绝(03 票)
    UNATTENDED = "unattended"      # 无人值守且无规则:拒绝并继续


class TurnOrigin(str, Enum):
    """回合来源(03 票):谁触发了这个回合。

    类型化通道传入 run_turn,取代心跳的文本前缀标记——文本可以伪造,枚举
    不可以。只有 human 可问人;其余(心跳/基准/未声明)构造上永不 interrupt,
    规则未命中的高危调用立即拒。缺省 unattended:来源不声明的调用方一律按
    无人值守(fail-closed,基准适配器零改动即落此形态)。
    """

    HUMAN = "human"          # 人:可 interrupt 问人
    HEARTBEAT = "heartbeat"  # 心跳:仅规则放行
    BENCH = "bench"          # 基准:仅规则放行
    UNATTENDED = "unattended"  # 未声明来源:按无人值守


@dataclass(frozen=True)
class ApprovalDecision:
    """审批应答:应答通道(TUI/Web)对 ApprovalRequest 的答案。

    approved 一次生效;persist=true 在批准之外按本次分级的动作×目标作用域
    铸规则(02 票 persist_rule),之后的同调用静默放行;source 与审计事件
    共用 DecisionSource,应答方按答复档位给 user_once/user_persist。
    """

    approved: bool
    persist: bool
    source: DecisionSource


def ensure_decision(value) -> ApprovalDecision:
    """应答值校验(fail-closed):不合规的应答一律按无人拒。

    覆盖:垃圾类型、伪造字段、source 传字符串而非枚举。守的是 interrupt
    resume 通道与引擎应答通道两处入口——不可信的应答不得放行任何高危。
    """
    if (isinstance(value, ApprovalDecision)
            and isinstance(value.approved, bool)
            and isinstance(value.persist, bool)
            and isinstance(value.source, DecisionSource)):
        return value
    return ApprovalDecision(approved=False, persist=False,
                            source=DecisionSource.UNATTENDED)


# 门拒绝话术的标志词:落进基准 _REJECT_MARKERS 词表,否则被误判为落地
REJECT_PHRASE = "审批门拒绝"


def rejection_text(tool_name: str, assessment: RiskAssessment,
                   source: DecisionSource = DecisionSource.UNATTENDED) -> str:
    """拒绝话术:带原因、点名具体动作,作为 tool_result 返回。

    按决定来源说话(04 票手工验收发现):人拒、超时拒、无人拒曾共用一句
    "无人值守",刚亲手拒绝的操作员被回"请在有人值守时再试"——拒绝的
    叙述必须与来路一致,agent 的后续行为才不跑偏。
    """
    head = (f"❌ {REJECT_PHRASE}：工具 {tool_name} 的本次调用属于必批副作用"
            f"（{assessment.risk_class}：{assessment.reason}）。")
    if source == DecisionSource.USER_ONCE:
        return head + "操作员已明确拒绝本次调用，未执行。请调整方案或与操作员确认后再试。"
    if source == DecisionSource.TIMEOUT:
        return (head + "审批等待超时（无人应答），已按拒绝处理，本次调用未执行。"
                "如确属日常合法操作，请在场及时应答或铸审批规则后再试。")
    return (head + "当前无人值守且无匹配审批规则，本次调用未执行。"
            "如属日常合法操作，请在有人交互时批准或铸审批规则后再试。")


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
                           source: DecisionSource,
                           rule_id: Optional[str] = None) -> None:
    event = {
        "thread_id": thread_id,
        "event": EVENT_APPROVAL_DECISION,
        "tool": tool_name,
        "approved": approved,
        "source": source.value,
        "risk_class": assessment.risk_class,
    }
    if rule_id is not None:
        # 命中规则的决定带 rule_id:事后能核"是哪条规则放的行"(仅规则路径携带)
        event["rule_id"] = rule_id
    audit_logger.log_event(**event)


def log_rule_persisted(thread_id: str, rule: dict) -> None:
    """规则铸成事件(02 票接线;形状此处定死:条目整体搭载)。"""
    audit_logger.log_event(
        thread_id=thread_id,
        event=EVENT_RULE_PERSISTED,
        rule=rule,
    )


def log_rule_revoked(thread_id: str, rule: dict) -> None:
    """规则撤销事件(批错的规则有回头路,回头路本身可查)。"""
    audit_logger.log_event(
        thread_id=thread_id,
        event=EVENT_RULE_REVOKED,
        rule=rule,
    )


def _rule_id(rule) -> Optional[str]:
    """从命中的规则取 id(兼容 dict 与 ApprovalRule 两种载体),进决定事件。"""
    rid = rule.get("id") if isinstance(rule, dict) else getattr(rule, "id", None)
    return str(rid) if rid is not None else None


def _attended(config: dict) -> bool:
    """本次调用是否来自可问人的回合:来源经 config.configurable 类型化传入。

    缺省(不声明来源)= 无人值守。config 由工具执行期的 ensure_config() 取得
    ——引擎把它放进 astream 的 configurable,工具链上下文原样携带。
    判定走 _turn_origin 同一解析(fail-closed 口径单源)。
    """
    return _turn_origin(config) == TurnOrigin.HUMAN


def _turn_origin(config: dict) -> TurnOrigin:
    """回合来源(类型化):非枚举值按未声明(fail-closed,与 _attended 同口径)。"""
    origin = (config.get("configurable") or {}).get("turn_origin", "")
    try:
        return TurnOrigin(origin)
    except ValueError:
        return TurnOrigin.UNATTENDED


def _mint_persist_rules(rule_store, thread_id: str, assessment: RiskAssessment) -> None:
    """persist=true 的批准铸规则(02 票 persist_rule 的主轨接线)。

    按本次分级的动作 × 每个目标作用域铸;提不出目标作用域(unclassified/
    外接)或动作不可铸时铸不出——批准仍只管本次,不放大。铸规则失败
    (含落盘 OSError)不影响本次批准照常执行,只留 system_action 可查。
    duck-typed 调 rule_store(门不 import 规则模块,避免环:rules 反向
    import 本模块)。
    """
    if rule_store is None or not assessment.targets:
        return
    for scope in assessment.targets:
        try:
            rule_store.persist_rule(action=assessment.risk_class, scope=scope,
                                    source="approval", thread_id=thread_id)
        except (ValueError, KeyError, OSError) as e:
            audit_logger.log_event(
                thread_id=thread_id, event="system_action",
                content=f"审批规则铸出失败,本次批准仍只生效一次:{e}")
            return


def _inner_args(tool: BaseTool, kwargs: dict) -> dict:
    """剥掉外层校验展开的可选字段默认 None,内层调用回到与无门时同一形态。

    双层校验问题(06 票 golden 实测暴露):外层包装的 pydantic 透传把
    "可选字段默认值"展开成显式 None 传进门,内层 tool.invoke 再校验时对
    str 字段的显式 None 直接抛 ValidationError——默认值只容许缺席,不容
    显式 null。无门时 ToolNode 单层校验、函数默认参收 None 相安无事
    (回归样本:modify_scheduled_task 只传 task_id+new_time 时报错)。
    只剥"可选字段 + None":必填字段的 None 保留,内层照常给出必填缺值的
    诚实报错;显式传 null 的可选字段与缺省同义,与无门路径等价。
    """
    schema = getattr(tool, "args_schema", None)
    fields = getattr(schema, "model_fields", None)
    if not fields:
        return kwargs
    return {k: v for k, v in kwargs.items()
            if not (v is None and not fields[k].is_required())}


def wrap_tool(
    tool: BaseTool,
    *,
    thread_id: str,
    provenance: Provenance = Provenance.BUILTIN,
    skill_folder: str = "",
    rule_matcher: Optional[RuleMatcher] = None,
    rule_store=None,
    hooks: Sequence[ToolHook] = (),
) -> StructuredTool:
    """给单个工具包上门:同名同 schema,调用先过 分级→规则→问人 链。

    hooks(03 票)是观察点:before 见每次经门调用的尝试,after/on_error 只
    见工具体执行段——hooks 只观察与记录,无否决权。无 hooks 时行为不变。
    工具体抛 DomainDenied 时,由本 wrapper 统一格式落拒绝回执并返回拒绝
    话术(回执单源,格式在 tools/domain_gate.py)。
    """

    def _decide(kwargs: dict, assessment: RiskAssessment, config: dict):
        """固定链主体。返回 (放行, 拒绝话术|None)。

        问人 = LangGraph interrupt:payload 是 ApprovalRequest 的字段形状,
        引擎侧重组为回合事件发出;应答 ApprovalDecision 经 Command resume
        回到本调用(同一次 invoke,批准与执行天然绑定同一份规范化参数)。
        """
        if not assessment.requires_approval:
            return True, None

        rule = rule_matcher(tool.name, kwargs, assessment) \
            if rule_matcher is not None else None
        if rule is not None:
            # 规则命中:没问过人,只有决定(见模块 docstring 的留痕语义)
            _log_approval_decision(thread_id, tool.name, assessment,
                                   approved=True, source=DecisionSource.RULE_AUTO,
                                   rule_id=_rule_id(rule))
            return True, None

        if _attended(config):
            decision = ensure_decision(interrupt({
                "tool": tool.name, "args": dict(kwargs),
                "risk_class": assessment.risk_class, "reason": assessment.reason,
            }))
            _log_approval_requested(thread_id, tool.name, kwargs, assessment)
            _log_approval_decision(thread_id, tool.name, assessment,
                                   approved=decision.approved,
                                   source=decision.source)
            if decision.approved:
                # 永久允许:决定留痕之后铸规则(rule_persisted 是决定的后果)
                if decision.persist:
                    _mint_persist_rules(rule_store, thread_id, assessment)
                return True, None
            return False, rejection_text(tool.name, assessment, decision.source)

        # 无人(心跳/基准/未声明来源):无规则且不问人 → 拒绝并继续
        _log_approval_requested(thread_id, tool.name, kwargs, assessment)
        _log_approval_decision(thread_id, tool.name, assessment,
                               approved=False, source=DecisionSource.UNATTENDED)
        return False, rejection_text(tool.name, assessment)

    def _new_call(kwargs: dict, config: dict) -> ToolCallContext:
        """分级 + 组装观察上下文(hooks 的只读面)。"""
        assessment = classify_tool_call(
            tool.name, kwargs, provenance=provenance, skill_folder=skill_folder)
        return ToolCallContext(
            tool=tool.name, args=kwargs, origin=_turn_origin(config),
            risk=assessment, started=monotonic())

    def _denied_outcome(denied: DomainDenied) -> str:
        """域名拒绝的统一回执:wrapper 单点落盘(03 票),话术原样返回。"""
        audit_logger.log_event(
            thread_id="system", event="system_action",
            content=domain_denied_audit_content(denied))
        return domain_denied_reply(denied)

    def _observe_error(ctx: ToolCallContext, exc: Exception) -> None:
        for h in hooks:
            h.on_error(ctx, exc)  # 默认实现再抛:hooks 只观察,无吞错权

    def _run_after_hooks(ctx: ToolCallContext, result):
        for h in hooks:
            result = h.after(ctx, result)
        return result

    def gated_run(**kwargs):
        config = ensure_config()
        ctx = _new_call(kwargs, config)
        for h in hooks:
            h.before(ctx)
        allowed, rejection = _decide(kwargs, ctx.risk, config)
        if not allowed:
            return rejection
        try:
            result = tool.invoke(_inner_args(tool, kwargs))
        except DomainDenied as denied:
            return _denied_outcome(denied)
        except Exception as exc:
            _observe_error(ctx, exc)
            raise
        return _run_after_hooks(ctx, result)

    async def gated_arun(**kwargs):
        config = ensure_config()
        ctx = _new_call(kwargs, config)
        for h in hooks:
            h.before(ctx)
        allowed, rejection = _decide(kwargs, ctx.risk, config)
        if not allowed:
            return rejection
        try:
            result = await tool.ainvoke(_inner_args(tool, kwargs))
        except DomainDenied as denied:
            return _denied_outcome(denied)
        except Exception as exc:
            _observe_error(ctx, exc)
            raise
        return _run_after_hooks(ctx, result)

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
    rule_store=None,
    hooks: Sequence[ToolHook] = (),
) -> List[BaseTool]:
    """装配点统一包装:内置/技能/外接全部过门。

    来源判定:外接名单内 → extra(默认必批);带 skill_folder 元数据 →
    skill(按命令收敛);其余 → builtin(查副作用册)。
    不做"已包装"短路:包装标记写在工具元数据里,而元数据来自被守对象
    (外接工具可自带任意元数据),守门判定不能握在被守者手里。
    hooks 原样传给每个包装件(同一观察点盖全部注册工具)。
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
            skill_folder=folder, rule_matcher=rule_matcher,
            rule_store=rule_store, hooks=hooks))
    return wrapped
