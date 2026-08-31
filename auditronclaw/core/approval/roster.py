"""静态名册装配(登记面收窄 02 票):core 静态册 ∪ 各域自报 → 合并册。

词汇见 CONTEXT.md「静态分级工具」:级别不随运行时状态的命名工具由所属域
装配期自报;条件分级工具(绑定域)不在此面,留 core 名册。合并规则是
ADR-002 裁定四:装配点把合并结果做成不可修改的名册注入审批门,分级器
builtin 判定只认这份册。装配期爆三类:跨来源同名(不论级别)、自报值出
静态词汇、动词打头却自报纯读(低报);忘登记由 meta-test 在测试期把守
(tests/test_static_roster.py)。

依赖方向:本模块在 core,只收 DomainRegistration 实参,不 import 任何域
(域的显式接线在装配点 agent.py,ADR-002 无自动发现)。
"""
from types import MappingProxyType
from typing import Mapping, Optional, get_args

from ..domain import DomainRegistration, RiskCategory
from .classifier import (
    RISK_READ,
    _BOUND_DOMAIN_TOOLS,
    _SHELL_TOOLS,
    _core_static_risk,
)

# 静态分级词汇全集:与 RiskCategory 同源(契约类型是唯一事实源)
_STATIC_CATEGORIES = frozenset(get_args(RiskCategory))

# ============ 动词钉子:自报名册的低报防线(治理条目) ============
#
# 判定:工具名首词(snake_case 第一节,整词匹配非子串)落在动词集内而
# 该名自报 read,视为低报,装配期 RuntimeError。豁免清单是唯一绕过通道。
#
# 【治理约定(只对这两张表)】动词集与豁免清单只增不删;任何增删都是治理
# 变更,必须进 PR 评审单独说明理由——两张表守的是"自报名册不被悄悄注水"
# 的后门,改表即改门。豁免清单首批为空:现存工具名无一首词落动词集且实为
# 纯读,不为假想的未来工具预造豁免;真需要时加一个名、评审一次。

RISKY_NAME_VERBS: frozenset = frozenset({
    # 首批 19 词 = 既有必批工具的首词(write/save/send/delete/modify/
    # submit/schedule/execute)+ 同族近义词。动词打头的名字声明纯读即当
    # 嫌疑,直到豁免清单点名
    "write", "save", "send", "delete", "remove", "modify", "update",
    "create", "append", "insert", "move", "copy", "rename", "upload",
    "download", "submit", "schedule", "execute", "run",
})

VERB_NAIL_EXEMPTIONS: frozenset = frozenset()


def _first_word(name: str) -> str:
    """工具名首词:snake_case 第一节,整词匹配(非子串)。"""
    return name.split("_", 1)[0].lower()


def _verb_nail_violation(name: str, category: str) -> Optional[str]:
    """钉子判定:命中返回人可读违规描述,未命中返回 None。"""
    if category != RISK_READ or name in VERB_NAIL_EXEMPTIONS:
        return None
    word = _first_word(name)
    if word in RISKY_NAME_VERBS:
        return (f"工具 '{name}' 名字首词 '{word}' 落在动词集而自报 read"
                f"(疑似低报);确属纯读须点名进豁免清单,清单变更走 PR 评审")
    return None


def build_static_risk(*registrations: DomainRegistration) -> Mapping[str, str]:
    """core 静态册 ∪ 各域自报 → 不可修改的合并静态册(装配期一次定死)。

    core 部分当刻从 classifier 三集构造(那是 core 静态册的存储形态,也是
    既有测试的 patch 注入点)。任何 RuntimeError 都发生在装配期——启动即爆,
    不留到心跳回合里静默不可用。
    """
    merged: dict = _core_static_risk()
    source_of: dict = {name: "core 静态册" for name in merged}
    for index, registration in enumerate(registrations):
        label = f"域自报 #{index}"
        for name, category in registration.risk.items():
            if category not in _STATIC_CATEGORIES:
                raise RuntimeError(
                    f"静态名册装配拒绝:工具 '{name}' 自报级别 '{category}' "
                    f"不在静态分级词汇 {sorted(_STATIC_CATEGORIES)}——条件分级"
                    f"工具(级别随运行时状态)留 core 名册,域不自报")
            if name in _BOUND_DOMAIN_TOOLS:
                raise RuntimeError(
                    f"静态名册装配拒绝:工具 '{name}' 已是 core 绑定域册的条件"
                    f"分级工具(绑定域名 {_BOUND_DOMAIN_TOOLS[name]}),域自报"
                    f"静态级别会遮蔽条件判定(自报:{category})")
            if name in _SHELL_TOOLS:
                raise RuntimeError(
                    f"静态名册装配拒绝:工具 '{name}' 是 shell 工具,分级按命令"
                    f"段判定、不涉册,域不得自报静态级别(自报:{category})")
            if name in merged:
                # 不论级别是否相同一律拒绝;级别只进报错信息,供人诊断
                raise RuntimeError(
                    f"静态名册装配拒绝:跨来源同名工具 '{name}' 同时来自 "
                    f"{source_of[name]}({merged[name]})与{label}({category})")
            violation = _verb_nail_violation(name, category)
            if violation is not None:
                raise RuntimeError(f"静态名册装配拒绝:{violation}")
            merged[name] = category
            source_of[name] = label
    return MappingProxyType(merged)
