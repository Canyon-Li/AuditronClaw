import os

from ..logger import audit_logger

# ============ 域名白名单（网络实名门第一落地） ============
#
# 原则：网络能力是命名工具，不是匿名 socket。两个命名网络工具
# （read_recent_emails / send_feishu_summary）在构造时绑定目标域，
# 守卫校验"本工具的目标域 ∈ 名单"——LLM 的工具参数里没有 URL 字段，
# 没有机会指定任意地址。守卫先于传输层执行，代码强制而非提示词约定。

# 默认名单：QQ 邮箱 IMAP 服务器 + 飞书 webhook 域
DEFAULT_ALLOWED_DOMAINS = {
    "imap.qq.com",
    "open.feishu.cn",
}

# 扩展名单缓存：refresh_extended_domains() 时刷新。守卫读这份缓存做判定，
# 自身不碰环境。
_EXTENDED_DOMAINS: set = set()

# 上次解析的环境变量 raw 值：raw 变化才重解析与重审计——判定路径经
# refresh 生产路径反复进来（05 票），环境扩展审计必须每次变化只留一次
_LAST_ENV_RAW: str | None = None

# 运行时审批规则缓存：审批门"永久允许"铸出的域名扩展规则作用域（域名
# 模式，如 open.feishu.cn / *.feishu.cn），refresh 时即时读盘。
# 名单三来源见 CONTEXT.md「域名白名单」：默认 ∪ 环境变量扩展 ∪ 运行时审批规则
_APPROVAL_DOMAIN_PATTERNS: list = []


def load_extended_domains():
    """
    读取环境变量追加的白名单域名（AUDITRONCLAW_ALLOWED_DOMAINS，逗号分隔）。

    与 shell 命令白名单（AUDITRONCLAW_ALLOWED_COMMANDS）同构：
    扩展白名单是一次显式的人工授权，读到非空扩展时记审计日志。
    """
    raw = os.getenv("AUDITRONCLAW_ALLOWED_DOMAINS", "")
    extended = {d.strip().lower() for d in raw.split(",") if d.strip()}
    if extended:
        audit_logger.log_event(
            thread_id="system",
            event="system_action",
            content=f"域名白名单扩展生效（AUDITRONCLAW_ALLOWED_DOMAINS）: {sorted(extended)}"
        )
    return extended


def load_approval_rule_domains() -> list:
    """
    读取运行时审批规则中域名扩展规则的作用域（域名模式）。

    即时读盘、每次全读——铸规则/撤销/夹具预置当次生效，不重启，与审批
    规则匹配（RuleStore 每次匹配读盘）同一机制。文件缺失/损坏=空模式集
    （fail-closed，守卫照常拒）。

    延迟导入打破环：approval.rules → classifier → 本模块，模块顶层 import
    会循环；调用期三者均已就绪。
    """
    from ..approval.classifier import RISK_DOMAIN_EXTEND
    from ..approval.rules import RuleStore
    return [rule.scope for rule in RuleStore().list_rules()
            if rule.action == RISK_DOMAIN_EXTEND]


def refresh_extended_domains() -> set:
    """
    刷新扩展名单缓存：环境变量 + 运行时审批规则（名单三来源的后两个）。

    生产调用路径（05 票）：check_domain_allowed 名单未命中时即调——审批门
    "永久允许"铸出的域名规则、规则撤销、环境变量调整，都在下一次判定
    生效，不重启。环境变量部分 raw 变化才重解析（扩展审计随之只记一次）。
    """
    global _EXTENDED_DOMAINS, _APPROVAL_DOMAIN_PATTERNS, _LAST_ENV_RAW
    raw = os.getenv("AUDITRONCLAW_ALLOWED_DOMAINS", "")
    if raw != _LAST_ENV_RAW:
        _LAST_ENV_RAW = raw
        _EXTENDED_DOMAINS = load_extended_domains()
    _APPROVAL_DOMAIN_PATTERNS = load_approval_rule_domains()
    return _EXTENDED_DOMAINS


def check_domain_allowed(domain: str) -> bool:
    """
    白名单守卫（纯判定）：工具绑定的目标域是否在名单内。

    名单 = 默认名单 ∪ 环境变量扩展 ∪ 运行时审批规则（CONTEXT.md「域名
    白名单」；审批门"永久允许"铸入的域名规则经 refresh 生产路径即时生效，
    见 refresh_extended_domains）。默认与扩展缓存命中即返回；未命中时先
    refresh 感知变化（环境变量恰逢其时变化会留一次扩展审计，见 refresh），
    再按规则作用域模式判定。拒绝时的审计事件由调用方（工具层）落。
    """
    domain = domain.strip().lower()
    if domain in DEFAULT_ALLOWED_DOMAINS | _EXTENDED_DOMAINS:
        return True
    refresh_extended_domains()
    if domain in DEFAULT_ALLOWED_DOMAINS | _EXTENDED_DOMAINS:
        return True
    from ..approval.rules import scope_matches  # 延迟导入（环在调用期解开）
    return any(scope_matches(pattern, domain)
               for pattern in _APPROVAL_DOMAIN_PATTERNS)


# 进程启动时加载一次环境变量扩展（与 shell 命令白名单的导入期加载同构）。
# 不经 refresh_extended_domains()：那会触发 approval 模块链回导本模块
# （延迟导入破环在"本模块经 feishu_tool/mail_tool 导入"的窗口内不成立）；
# 审批规则部分即时读盘，无需导入期预热。
_LAST_ENV_RAW = os.getenv("AUDITRONCLAW_ALLOWED_DOMAINS", "")
_EXTENDED_DOMAINS = load_extended_domains()
