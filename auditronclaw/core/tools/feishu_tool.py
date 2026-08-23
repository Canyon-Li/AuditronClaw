import json
import os
from typing import Callable, Optional
from urllib import request as urllib_request
from urllib.error import URLError

from .base import auditronclaw_tool
from .domain_gate import check_domain_allowed
from ..logger import audit_logger

# ============ 飞书推送命名工具 ============
#
# 网络实名门的第一落地：本工具构造时绑定目标域（飞书 webhook 域），
# 域名守卫先于传输层执行（代码强制，非提示词约定）。
# webhook URL 只在工具实现内部从 .env 读取，不进工具参数、返回值与审计日志。


# 目标域引用白名单常量（名单是单一事实源，不在两处写字面量）
FEISHU_WEBHOOK_DOMAIN = "open.feishu.cn"
assert FEISHU_WEBHOOK_DOMAIN in {
    "imap.qq.com",
    "open.feishu.cn",
}, "飞书工具绑定的目标域必须先入默认名单"
_WEBHOOK_TIMEOUT_SECONDS = 10

# 传输层注入缝（接缝 B）：模块内可替换的 sender，测试与基准注入假实现，零真实网络。
# 默认 None = 生产通道（_http_sender）。不作为工具参数——LLM 的参数面里没有它。
_active_sender: Optional[Callable] = None


def set_sender(sender: Optional[Callable]) -> None:
    """注入/还原传输层 sender（测试与基准专用；生产不调用，走真实 POST）。"""
    global _active_sender
    _active_sender = sender


def get_feishu_webhook_url() -> Optional[str]:
    """
    从环境读取飞书自定义机器人 webhook URL（宿主机信任面，存 .env）。
    未配置返回 None——凭据永远不进参数、返回值与审计日志。
    """
    return os.getenv("FEISHU_WEBHOOK_URL") or None


def _http_sender(webhook_url: str, payload: dict) -> dict:
    """
    生产通道：向 webhook 真实 POST JSON。
    零第三方依赖，用标准库 urllib；测试用注入的假 sender 替换，零网络。
    """
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=_WEBHOOK_TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


@auditronclaw_tool
def send_feishu_summary(summary_text: str) -> str:
    """
    把一段文本摘要推送到作者指定的飞书群（自定义机器人）。
    适用于每日邮箱事务台摘要、待办提醒等"推给手机看"的场景。

    【推送边界（代码强制，非约定）】：
    1. 目标域名固定为飞书 webhook 域（open.feishu.cn），经域名白名单守卫校验，
       你无法通过本工具把内容发往任意地址——没有 URL 参数可填。
    2. webhook URL 等凭据只存在宿主机 .env，不会出现在参数、返回值或审计日志中。
    3. 本工具只推送文本，不做飞书 API 的其他操作。

    参数 summary_text 为要推送的完整文本（建议使用「分类账」格式日报）。
    """
    try:
        # 0. 凭据前置检查：未配置时不碰网络，返回结构化错误
        webhook_url = get_feishu_webhook_url()
        if not webhook_url:
            return (
                "❌ 推送失败：飞书 webhook 未配置（FEISHU_WEBHOOK_URL）。"
                "请部署者在宿主机 .env 中配置后再试。"
            )

        # 1. 域名白名单守卫：先于传输层执行，名单外拒绝并落审计
        if not check_domain_allowed(FEISHU_WEBHOOK_DOMAIN):
            audit_logger.log_event(
                thread_id="system",
                event="system_action",
                content=(
                    f"域名白名单拦截：工具 {send_feishu_summary.name} 目标域 "
                    f"'{FEISHU_WEBHOOK_DOMAIN}' 不在允许名单内，推送被拒绝。"
                    "如需扩展，请设置 AUDITRONCLAW_ALLOWED_DOMAINS 环境变量。"
                ),
            )
            return (
                f"❌ 推送失败：目标域名 '{FEISHU_WEBHOOK_DOMAIN}' 不在允许名单内，"
                "本次推送已被域名白名单拦截并记录审计。"
            )

        # 2. 传输层：生产真实 POST / 测试注入的假 sender（零网络）
        active_sender = _active_sender if _active_sender is not None else _http_sender
        payload = {"msg_type": "text", "content": {"text": summary_text}}
        response = active_sender(webhook_url, payload)

        # 3. 脱敏回执：只有成功/失败与飞书返回码，无 URL 无凭据
        audit_logger.log_event(
            thread_id="system",
            event="system_action",
            content=(
                f"飞书推送回执：目标域 {FEISHU_WEBHOOK_DOMAIN}，"
                f"msg_type=text，响应码 {response.get('code', 'unknown')}，"
                f"内容 {len(summary_text)} 字符。"
            ),
        )
        return (
            f" ✅ 飞书推送成功：已向群机器人发送 {len(summary_text)} 字符摘要，"
            f"飞书响应码 {response.get('code', 'unknown')}。"
        )
    except URLError as e:
        audit_logger.log_event(
            thread_id="system",
            event="system_action",
            content=f"飞书推送失败（网络层）：目标域 {FEISHU_WEBHOOK_DOMAIN}，错误类型 {type(e).__name__}。",
        )
        return f"❌ 推送失败（网络层）：{type(e).__name__}。请稍后重试，待办与摘要内容未受影响。"
    except Exception as e:
        # 结构化错误兜底：不把裸异常抛给 LLM。只报错误类型不透传 str(e)——
        # urllib 家族异常的 message 常内嵌完整请求 URL，透传即凭据泄露。
        error_name = type(e).__name__
        audit_logger.log_event(
            thread_id="system",
            event="system_action",
            content=(
                f"飞书推送失败：目标域 {FEISHU_WEBHOOK_DOMAIN}，"
                f"错误 {error_name}。"
            ),
        )
        return (
            f"❌ 推送失败（{error_name}）。请稍后重试，待办与摘要内容未受影响。"
        )
