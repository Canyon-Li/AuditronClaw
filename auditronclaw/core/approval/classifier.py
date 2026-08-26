"""副作用分级器(纯函数):按操作的副作用而非工具名定风险。

词汇见 CONTEXT.md「副作用分级」:读类免批;写、删、解释器执行、白名单扩展
必批;shell 工具按命令段判定——段内全为纯读命令才免批,出现解释器、变更
命令或重定向即整条必批。分级结果是规则匹配的输入:规则只能豁免对应级别
的动作,改变不了分级本身。

新工具入册即加映射:内置工具名查册;未入册(unclassified)默认必批,不猜。
"""
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ..skill_loader import expand_skill_command
from ..tools.domain_gate import check_domain_allowed
from ..tools.mail_tool import IMAP_DOMAIN
from ..tools.feishu_tool import FEISHU_WEBHOOK_DOMAIN
from ..tools.sandbox_tools import (
    _BASE_ALLOWED_COMMANDS,
    _CMD_VAR_PATTERN,
    _FIND_HAZARD_FLAGS,
    _INTERPRETERS,
    _EXPANSION_PATTERN,
    _SEGMENT_SPLIT_PATTERN,
    _REDIRECTION_TARGET_PATTERN,
    _parse_segment_head,
    _segment_head_name,
)

# ============ 风险级(值与 02 票规则的"动作"字段、03 票 ApprovalRequest.risk_class 共用) ============

RISK_READ = "read"                # 免批:无副作用
RISK_WRITE = "write"              # 必批:落盘/覆写
RISK_DELETE = "delete"            # 必批:不可逆删除
RISK_EXECUTE = "execute"          # 必批:解释器执行/重定向(jail_010 断链点)
RISK_DOMAIN_EXTEND = "domain_extend"  # 必批:绑定域在白名单外的网络调用(扩展流程归 05 票)
RISK_UNCLASSIFIED = "unclassified"    # 必批:未入副作用册,默认不猜

# 混合段整条必批:多个必批级并存时取此序最前者,标签确定、审计可读
_HAZARD_PRECEDENCE = (RISK_DELETE, RISK_EXECUTE, RISK_WRITE, RISK_DOMAIN_EXTEND, RISK_UNCLASSIFIED)


@dataclass(frozen=True)
class RiskAssessment:
    """分级结果:级别 + 人可读依据(审批提示与审计共用)。"""

    tool: str
    risk_class: str
    reason: str

    @property
    def requires_approval(self) -> bool:
        return self.risk_class != RISK_READ


# ============ 工具册(新工具入册即加映射) ============

# 纯读工具:参数面无副作用
_PURE_READ_TOOLS = frozenset({
    "get_current_time",
    "calculator",
    "list_office_files",
    "read_office_file",
    "get_system_model_info",
    "list_scheduled_tasks",
})

# 绑定白名单内域名的推送/拉取:绑定域在名单内即免批(网络实名门已过);
# 名单外 → domain_extend 必批(扩展流程归 05 票)
_BOUND_DOMAIN_TOOLS = {
    "read_recent_emails": IMAP_DOMAIN,
    "send_feishu_summary": FEISHU_WEBHOOK_DOMAIN,
}

# 写类(落盘/覆写)
_WRITE_TOOLS = frozenset({
    "write_office_file",
    "save_user_profile",
    "schedule_task",
    "modify_scheduled_task",
    "submit_mailbox_desk_report",
})

# 删类(不可逆)
_DELETE_TOOLS = frozenset({"delete_scheduled_task"})

# shell 工具:命令段级判定
_SHELL_TOOLS = frozenset({"execute_office_shell"})

# 工具注册来源(provenance):装配点在包装时告知每个工具从哪来,分级按来源
# 走不同路径。枚举风格与 gate.DecisionSource 同构
class Provenance(str, Enum):
    BUILTIN = "builtin"  # 内置册(含 tools= 直给的名册)
    EXTRA = "extra"      # 外接工具:不经命令白名单与路径防护,默认必批
    SKILL = "skill"      # 技能工具:经 execute_office_shell 收敛,按命令判


def _assess(tool: str, risk_class: str, reason: str) -> RiskAssessment:
    return RiskAssessment(tool=tool, risk_class=risk_class, reason=reason)


def classify_tool_call(
    tool_name: str,
    args: Mapping,
    *,
    provenance: Provenance | str = Provenance.BUILTIN,
    skill_folder: str = "",
) -> RiskAssessment:
    """对一次工具调用做副作用分级(纯判定,不执行任何操作)。

    - builtin:按工具名查册;未入册 → unclassified 必批
    - extra:外接工具一律 unclassified 必批(它不经命令白名单与路径防护)
    - skill:mode=help 纯读;mode=run 按其最终交给 execute_office_shell 的
      命令段级判定(与 shell 工具同源,{baseDir} 同规则替换)
    """
    if provenance == Provenance.EXTRA:
        return _assess(tool_name, RISK_UNCLASSIFIED,
                       "外接工具未入副作用册,默认必批(不经命令白名单与路径防护)")
    if provenance == Provenance.SKILL:
        return _classify_skill_call(tool_name, args, skill_folder)
    return _classify_builtin_call(tool_name, args)


def _classify_skill_call(tool_name: str, args: Mapping, skill_folder: str) -> RiskAssessment:
    mode = args.get("mode", "")
    if mode != "run":
        # help 读说明书 / 非法 mode(工具自身报错):无副作用
        return _assess(tool_name, RISK_READ, "技能工具读说明书,无副作用")
    command = str(args.get("command", ""))
    # 与懒执行器同一替换(单一事实源):批的命令就是跑的命令
    actual_cmd = expand_skill_command(command, skill_folder) if skill_folder else command
    return classify_shell_command(tool_name, actual_cmd)


def _write_target(args: Mapping) -> str:
    """写类工具的落点提示:有路径报路径,没路径报落点类别。"""
    if args.get("filepath"):
        return str(args["filepath"])
    if args.get("new_content") is not None:
        return "memory/profiles 画像文件"
    return "tasks.json 任务队列"


def _classify_builtin_call(tool_name: str, args: Mapping) -> RiskAssessment:
    if tool_name in _PURE_READ_TOOLS:
        return _assess(tool_name, RISK_READ, "纯读工具,无副作用")

    if tool_name in _BOUND_DOMAIN_TOOLS:
        domain = _BOUND_DOMAIN_TOOLS[tool_name]
        if check_domain_allowed(domain):
            return _assess(tool_name, RISK_READ,
                           f"绑定白名单内域名 {domain} 的网络实名工具")
        return _assess(tool_name, RISK_DOMAIN_EXTEND,
                       f"绑定域名 {domain} 不在白名单内,属白名单扩展流程")

    if tool_name in _WRITE_TOOLS:
        return _assess(tool_name, RISK_WRITE,
                       f"写类副作用(目标:{_write_target(args)})")

    if tool_name in _DELETE_TOOLS:
        return _assess(tool_name, RISK_DELETE,
                       f"不可逆删除(目标:{args.get('task_id', '未知')})")

    if tool_name in _SHELL_TOOLS:
        return classify_shell_command(tool_name, str(args.get("command", "")))

    return _assess(tool_name, RISK_UNCLASSIFIED, "工具未入副作用册,默认必批")


# ============ shell 段级判定(与命令校验同源:解析复用 sandbox_tools 的 helper) ============

# 只读命令子集:与命令白名单基础集的"查看/搜索/杂项"对齐。
# find 特例:纯搜索免批,但执行族参数(-exec/-delete/-fprint 等)让它能
# 执行/写删——白名单层拒绝该形态(_validate_find_segment),分级层对同一
# 参数清单(sandbox_tools._FIND_HAZARD_FLAGS)按执行类必批
_SHELL_READ_COMMANDS = frozenset({"ls", "dir", "cat", "type", "pwd", "grep", "findstr", "find", "echo"})
# 变更命令:文件 CRUD 里按写/删分
_SHELL_WRITE_COMMANDS = frozenset({"mv", "move", "cp", "copy", "mkdir", "md", "touch"})
_SHELL_DELETE_COMMANDS = frozenset({"rm", "del", "rmdir", "rd"})


def classify_shell_command(tool_name: str, command: str) -> RiskAssessment:
    """对一条 shell 命令做段级分级(纯判定,不执行)。

    与命令校验同源:切段、shlex 首 token、文件名归一化全部复用
    sandbox_tools 的解析 helper——校验要拒的命令不会被分级判成纯读。
    任一段含解释器或重定向即整条必批(jail_010 断链点)。
    """
    if not command or not command.strip():
        return _assess(tool_name, RISK_READ, "空命令,无副作用")

    # 展开/替换语法:字符级拒绝的对象同样无法按段定级,不猜
    if _EXPANSION_PATTERN.search(command) or _percent_var_pattern(command):
        return _assess(tool_name, RISK_UNCLASSIFIED,
                       f"含展开/替换语法,无法按段定级(命令:{command})")

    # 重定向:输出落盘是写副作用,与解释器同门,整条必批
    if any(_REDIRECTION_TARGET_PATTERN.finditer(command)):
        return _assess(tool_name, RISK_EXECUTE,
                       f"含重定向(命令:{command})")

    hazards = []  # [(risk_class, 触发段)]
    for segment in _SEGMENT_SPLIT_PATTERN.split(command):
        stripped = segment.strip()
        if not stripped:
            continue
        try:
            tokens = _parse_segment_head(stripped)
            head = _segment_head_name(tokens).lower() if tokens else ""
        except PermissionError:
            return _assess(tool_name, RISK_UNCLASSIFIED,
                           f"命令解析失败,无法按段定级(段:{stripped})")
        if head in _INTERPRETERS:
            hazards.append((RISK_EXECUTE, stripped))
        elif head in _SHELL_DELETE_COMMANDS:
            hazards.append((RISK_DELETE, stripped))
        elif head in _SHELL_WRITE_COMMANDS:
            hazards.append((RISK_WRITE, stripped))
        elif head in _SHELL_READ_COMMANDS:
            if head == "find" and _find_has_hazard_flag(tokens):
                hazards.append((RISK_EXECUTE, stripped))
            continue
        else:
            hazards.append((RISK_UNCLASSIFIED, stripped))

    if not hazards:
        return _assess(tool_name, RISK_READ, "各命令段均为纯读命令")

    risk_class = min(hazards, key=lambda h: _HAZARD_PRECEDENCE.index(h[0]))[0]
    triggers = "; ".join(seg for _cls, seg in hazards)
    return _assess(tool_name, risk_class, f"必批命令段:{triggers}")


def _percent_var_pattern(command: str) -> bool:
    """cmd 风格 %VAR% 展开(成对出现才算变量)——与 _validate_command 同一正则。"""
    return bool(_CMD_VAR_PATTERN.search(command))


def _find_has_hazard_flag(tokens: list) -> bool:
    """find 段是否携带执行/删除/写文件族参数(与命令白名单共用同一清单)。"""
    return any(tok.lower() in _FIND_HAZARD_FLAGS for tok in tokens[1:])
