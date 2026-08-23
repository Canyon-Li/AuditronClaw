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

# 扩展名单缓存：refresh_extended_domains() 时刷新（raw 变化才重解析，
# 避免重复解析与重复审计）。守卫读这份缓存做判定，自身不碰环境。
_EXTENDED_DOMAINS: set = set()


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


def refresh_extended_domains() -> set:
    """
    刷新扩展名单缓存：环境变量 raw 值变化时重解析并留一次审计。

    副作用集中在这一处（进程启动时、以及需要感知运行期环境变化时调用），
    check_domain_allowed 保持纯判定。
    """
    global _EXTENDED_DOMAINS
    _EXTENDED_DOMAINS = load_extended_domains()
    return _EXTENDED_DOMAINS


# 进程启动时加载一次（与 shell 命令白名单的导入期加载同构）
refresh_extended_domains()


def check_domain_allowed(domain: str) -> bool:
    """
    白名单守卫（纯判定）：工具绑定的目标域是否在名单内。

    名单 = 默认名单 ∪ 环境变量扩展缓存；名单内放行，名单外拒绝。
    只读快照、无副作用——环境变化的感知由 refresh_extended_domains()
    负责，拒绝时的审计事件由调用方（工具层）落。
    """
    return domain.strip().lower() in DEFAULT_ALLOWED_DOMAINS | _EXTENDED_DOMAINS
