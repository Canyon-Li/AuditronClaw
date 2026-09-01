"""审批规则(02 票):高危的唯一豁免通道。

词汇见 CONTEXT.md「审批规则」:一次"永久允许"入规则的持久化授权,绑定动作
与目标作用域,条目带出处。规则之外的高危在无人时一律拒、拒绝并继续。

- 落点:workspace 级 approval_rules.json,在 office 之外——agent 的写面被
  路径校验挡在 office 内,够不着自己的规则
- 匹配纯函数:动作(=副作用分级级别)相等且调用全部目标作用域被规则覆盖
  才算命中;提不出目标作用域的调用(未入册/外接)规则不豁免,不猜
- 即时性:每次匹配读盘——入规则与撤销当次生效,不重启(05 票域名扩展的
  运行期生效同走此机制)
- 冷启动:规则文件不存在=空规则集,门照常工作(拒绝并继续),不报错不跳过;
  文件损坏或含非法条目按 fail-closed 处理(空集/跳过该条)

入规则的主轨是审批交互"永久允许"(03/04 票接线),本票落内部接口
persist_rule;清单与撤销是管理面(04 票 TUI 复用)。
"""
import fnmatch
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from ..logger import get_audit_logger
from .classifier import (
    RISK_DELETE,
    RISK_DOMAIN_EXTEND,
    RISK_EXECUTE,
    RISK_WRITE,
    RiskAssessment,
)
from .gate import log_rule_persisted, log_rule_revoked

# 可入规则的动作集:必批级。read 无从豁免;unclassified 提不出目标作用域,
# 规则世界不收(不是拒绝写入,是从机制上无法命中)
APPROVAL_ACTIONS = frozenset({RISK_WRITE, RISK_DELETE, RISK_EXECUTE, RISK_DOMAIN_EXTEND})


class RuleSource(str, Enum):
    """规则出处:每条豁免可枚举其正当性(审计叙事的主体)。"""

    APPROVAL = "approval"            # 审批交互"永久允许"入规则(主轨,03/04 接线)
    CLI = "cli"                      # 命令行管理面(预留)
    BENCH_FIXTURE = "bench_fixture"  # 基准夹具预置(06 票)


@dataclass(frozen=True)
class ApprovalRule:
    """条目 schema:id / 动作 / 目标作用域 / 出处 / 创建时间。

    动作取值=副作用分级级别(write/delete/execute/domain_extend);
    作用域是 workspace 相对路径模式(office/scripts/**、tasks.json、
    memory/profiles/**)或域名(open.feishu.cn、*.feishu.cn)。
    """

    id: str
    action: str
    scope: str
    source: str
    created_at: str

    def to_dict(self) -> dict:
        """审计(rule_persisted/rule_revoked)与落盘共用的条目形状。"""
        return {
            "id": self.id,
            "action": self.action,
            "scope": self.scope,
            "source": self.source,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> Optional["ApprovalRule"]:
        """从落盘条目解析;非法条目返回 None(跳过,fail-closed 不报错)。"""
        try:
            action = raw["action"]
            scope = _check_scope(raw["scope"])
            source = RuleSource(raw["source"]).value  # 出处必须是枚举值
            rule_id = str(raw["id"])
            created_at = str(raw["created_at"])
        except (KeyError, TypeError, ValueError):
            return None
        if action not in APPROVAL_ACTIONS:
            return None
        if not rule_id or not created_at:
            return None
        return cls(id=rule_id, action=action, scope=scope,
                   source=source, created_at=created_at)


# ============ 作用域模式匹配(纯函数:攻击与合法的分界线) ============

def _segments(path: str) -> List[str]:
    """统一小写、正斜杠后切段(Windows 文件系统大小写不敏感,授权语义跟随)。"""
    return path.replace("\\", "/").lower().split("/")


def _match_segments(pattern: List[str], target: List[str], pi: int, ti: int) -> bool:
    if pi == len(pattern):
        return ti == len(target)
    if pattern[pi] == "**":
        # ** 匹配零或多段:子树(含目录自身)
        return any(_match_segments(pattern, target, pi + 1, tj)
                   for tj in range(ti, len(target) + 1))
    if ti == len(target):
        return False
    # * ? 只在段内匹配(段内可跨字符:域名的 *.feishu.cn 依赖此语义)
    if not fnmatch.fnmatchcase(target[ti], pattern[pi]):
        return False
    return _match_segments(pattern, target, pi + 1, ti + 1)


def scope_matches(pattern: str, target: str) -> bool:
    """作用域模式匹配:路径感知 glob(纯判定)。

    段以 / 切分;** 匹配零或多段;* ? 仅段内。这是规则放行的判定线,
    语义必须可预测:office/scripts/** 不匹配 office 根目录脚本,也不匹配
    同前缀兄弟目录 office/scripts_evil/。
    """
    return _match_segments(_segments(pattern), _segments(target), 0, 0)


def rule_matches(rule: ApprovalRule, assessment: RiskAssessment) -> bool:
    """规则匹配(纯函数):门放行的唯一自动依据。

    命中条件:动作与分级级别相等,且调用的全部目标作用域都被规则覆盖
    (部分覆盖不算——mv 触碰作用域外文件时不得被自动放行)。规则放行的
    是"对哪里的什么动作",不是"这类操作";提不出目标作用域的调用
    (未入册/外接)任何规则都不豁免,不猜。
    """
    if rule.action != assessment.risk_class:
        return False
    if not assessment.targets:
        return False
    return all(scope_matches(rule.scope, target) for target in assessment.targets)


# ============ 规则存取:门面 + 原子落盘 ============

def _check_scope(scope) -> str:
    """作用域合法性(单一判定,from_dict 与 persist_rule 共用):非空、无首尾空白。"""
    if not isinstance(scope, str) or not scope.strip() or scope != scope.strip():
        raise ValueError(f"作用域 {scope!r} 非法(非空、无首尾空白)")
    return scope


def _utc_now_iso() -> str:
    """创建时间:与审计日志同一时间戳格式(UTC,Z 后缀)。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RuleStore:
    """规则文件的门面:读即时读盘,写原子落盘。

    每次匹配重新读文件而非构造时缓存——入规则与撤销都要运行期即时生效
    (单操作员低频调用,文件极小,读取代价可忽略)。
    """

    def __init__(self, path: str):
        # 落点为装配期入参(05 票):入口从 WorkspaceConfig 注入,本模块不持路径常量
        self.path = path

    # ---------- 读 ----------

    def _load(self) -> List[ApprovalRule]:
        """加载规则;文件不存在=空规则集(冷启动),损坏=空集(fail-closed)。"""
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return []
        except (OSError, ValueError, UnicodeDecodeError) as e:
            get_audit_logger().log_event(
                thread_id="system", event="system_action",
                content=f"审批规则文件不可读,按空规则集处理(fail-closed): {e}")
            return []
        if not isinstance(raw, list):
            return []
        rules = []
        for entry in raw:
            rule = ApprovalRule.from_dict(entry) if isinstance(entry, dict) else None
            if rule is not None:
                rules.append(rule)
        return rules

    def list_rules(self) -> List[ApprovalRule]:
        """规则清单(管理面:清单可查,落盘序=创建序)。"""
        return self._load()

    def match(self, assessment: RiskAssessment) -> Optional[ApprovalRule]:
        """按分级结果找第一条命中的规则(落盘序优先,结果确定可排查)。"""
        for rule in self._load():
            if rule_matches(rule, assessment):
                return rule
        return None

    # ---------- 写 ----------

    def persist_rule(self, action: str, scope: str, source: str,
                     thread_id: str = "system") -> ApprovalRule:
        """入规则("永久允许"的落点):条目落盘 + rule_persisted 入审计。

        幂等:同动作同作用域已存在时返回既有条目,不重复写、不重复留痕。
        03/04 票的 ApprovalDecision(persist=true) 接线到本接口。
        """
        if action not in APPROVAL_ACTIONS:
            raise ValueError(
                f"动作 {action!r} 不可入规则(可入规则的动作:{sorted(APPROVAL_ACTIONS)})")
        scope = _check_scope(scope)
        source = RuleSource(source).value  # 非法出处直接 ValueError

        rules = self._load()
        for rule in rules:
            if rule.action == action and rule.scope == scope:
                return rule
        rule = ApprovalRule(id=uuid.uuid4().hex, action=action, scope=scope,
                            source=source, created_at=_utc_now_iso())
        self._save(rules + [rule])
        log_rule_persisted(thread_id, rule.to_dict())
        return rule

    def revoke_rule(self, rule_id: str, thread_id: str = "system") -> ApprovalRule:
        """撤销规则:即失效(下次匹配读不到)并留审计;不存在则 KeyError。"""
        rules = self._load()
        for idx, rule in enumerate(rules):
            if rule.id == rule_id:
                removed = rules.pop(idx)
                self._save(rules)
                log_rule_revoked(thread_id, removed.to_dict())
                return removed
        raise KeyError(f"规则不存在: {rule_id}")

    def _save(self, rules: List[ApprovalRule]) -> None:
        """原子落盘:临时文件 + os.replace,规则文件不出现半截状态。"""
        target_dir = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(target_dir, exist_ok=True)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump([rule.to_dict() for rule in rules], f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)


def make_rule_matcher(store: RuleStore):
    """门侧适配器:gate 的 RuleMatcher 契约 → 规则匹配。

    契约(见 gate.py):rule_matcher(tool_name, args, assessment) ->
    命中的规则或 None。匹配只依赖分级结果(级别 + 目标作用域),工具名与
    参数已经进过分级,这里不再看第二眼。
    """
    def rule_matcher(tool_name: str, args: dict, assessment: RiskAssessment):
        return store.match(assessment)
    return rule_matcher
