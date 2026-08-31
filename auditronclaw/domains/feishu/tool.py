import json
import os
from typing import Callable, Optional
from urllib import request as urllib_request
from urllib.error import URLError

from ...core.approval.hooks import Receipt
from ...core.domain import DomainRegistration
from ...core.tools.base import auditronclaw_tool
from ...core.tools.domain_gate import (
    DEFAULT_ALLOWED_DOMAINS,
    require_domain,
)
from ...core.tools.egress import EgressChannel, register_egress_channel

# ============ 飞书推送命名工具 ============
#
# 网络实名门的第一落地：本工具构造时绑定目标域（飞书 webhook 域），
# 域名守卫先于传输层执行（代码强制，非提示词约定）。
# webhook URL 只在工具实现内部从 .env 读取，不进工具参数、返回值与审计日志。
# 域包纪律（ADR-002）：回执全走 Receipt→hooks（域内不直接写审计日志，钉子
# 见本目录 test_tool.py）；拒绝话术单源在 core 的 domain_gate——工具体一行
# require_domain 抛 DomainDenied，由审批门 wrapper 统一格式落拒绝回执。


# 目标域引用白名单常量（名单是单一事实源，不在两处写字面量）
FEISHU_WEBHOOK_DOMAIN = "open.feishu.cn"
assert FEISHU_WEBHOOK_DOMAIN in DEFAULT_ALLOWED_DOMAINS, \
    "飞书工具绑定的目标域必须先入默认名单"
_WEBHOOK_TIMEOUT_SECONDS = 10

# 传输层注入点：模块内可替换的 sender，测试与基准注入假实现，零真实网络。
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


# 出站通道登记（03 票）：与传输定义同文件。哨兵深度=注入点 _http_sender——
# 它本身就是网络边界（urlopen 在其体内），测试一律经注入假 sender 零网络。
# domain 是本通道绑定的网络实名：core 绑定域册以字面量登记同名域名，
# 一致性由 tests/test_domain_name_consistency.py 把守（ADR-002 裁定 3）。
def _get_http_sender():
    return _http_sender


def _set_http_sender(transport) -> None:
    global _http_sender
    _http_sender = transport


_EGRESS_FEISHU_WEBHOOK = EgressChannel(
    name="feishu_webhook",
    module=__name__,
    getter=_get_http_sender,
    setter=_set_http_sender,
    guard="守注入点 _http_sender（它本身就是网络边界）；测试请用 "
          "helpers.InjectedSender 注入假 sender，零真实网络",
    domain=FEISHU_WEBHOOK_DOMAIN,
)

register_egress_channel(_EGRESS_FEISHU_WEBHOOK)


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
    _, message = push_text_via_bound_domain(
        summary_text, tool_name=send_feishu_summary.name
    )
    return message


def push_text_via_bound_domain(summary_text: str, tool_name: str):
    """
    命名推送的核心路径：凭据检查 → 域名门 → 活动 sender → 脱敏回执。
    供 send_feishu_summary 与事务台提交工具共用——同一注入点（_active_sender）、
    同一道域名门、同一套审计词汇（03 票起回执随 Receipt 返回值走，由
    wrapper 的 AuditReceiptHook 单源落盘）。返回 (是否成功, 给 LLM 看的
    脱敏回执文案)：文案在成功/失败时是 Receipt（str 子类，audit_content
    携带审计内容，供调用方搭载转发），未配置凭据时是普通 str——该分支
    不落审计，与迁移前一致。
    域名门外抛 DomainDenied：直接工具（send_feishu_summary）不捕获，由
    审批门 wrapper 统一转拒绝结果；事务台提交工具显式处理（待办已落盘
    的如实陈述，话术仍取 domain_gate 单源）。
    """
    # 0. 凭据前置检查：未配置时不碰网络，返回结构化错误
    webhook_url = get_feishu_webhook_url()
    if not webhook_url:
        return False, (
            "❌ 推送失败：飞书 webhook 未配置（FEISHU_WEBHOOK_URL）。"
            "请部署者在宿主机 .env 中配置后再试。"
        )

    # 1. 域名门（工具体内的一行）：名单外抛 DomainDenied，由审批门 wrapper
    #    统一格式落拒绝回执并返回拒绝话术——检查留在工具体内，回执不落工具体
    require_domain(FEISHU_WEBHOOK_DOMAIN, tool_name=tool_name, action="推送")

    try:
        # 2. 传输层：生产真实 POST / 测试注入的假 sender（零网络）
        active_sender = _active_sender if _active_sender is not None else _http_sender
        payload = {"msg_type": "text", "content": {"text": summary_text}}
        response = active_sender(webhook_url, payload)

        # 3. 脱敏回执：只有成功/失败与飞书返回码，无 URL 无凭据——内容随
        #    返回值走，落盘由 wrapper 的 AuditReceiptHook 单源执行
        return True, Receipt(
            f" ✅ 飞书推送成功：已向群机器人发送 {len(summary_text)} 字符摘要，"
            f"飞书响应码 {response.get('code', 'unknown')}。",
            f"飞书推送回执：目标域 {FEISHU_WEBHOOK_DOMAIN}，"
            f"msg_type=text，响应码 {response.get('code', 'unknown')}，"
            f"内容 {len(summary_text)} 字符。",
        )
    except URLError as e:
        return False, Receipt(
            f"❌ 推送失败（网络层）：{type(e).__name__}。请稍后重试，待办与摘要内容未受影响。",
            f"飞书推送失败（网络层）：目标域 {FEISHU_WEBHOOK_DOMAIN}，错误类型 {type(e).__name__}。",
        )
    except Exception as e:
        # 结构化错误兜底：不把裸异常抛给 LLM。只报错误类型不透传 str(e)——
        # urllib 家族异常的 message 常内嵌完整请求 URL，透传即凭据泄露。
        error_name = type(e).__name__
        return False, Receipt(
            f"❌ 推送失败（{error_name}）。请稍后重试，待办与摘要内容未受影响。",
            f"飞书推送失败：目标域 {FEISHU_WEBHOOK_DOMAIN}，错误 {error_name}。",
        )


def register() -> DomainRegistration:
    """feishu 域登记（ADR-002 分工表的接线槽位，每域恰好一个 register()）。

    risk 为空是设计结果非遗漏：send_feishu_summary 是绑定域工具（条件
    分级——级别依赖域名白名单当刻内容），按 ADR-002 裁定 1 留 core 名册、
    域不自报。egress 引用同文件登记的通道（登记与定义同址，register()
    只引用不重建）。
    """
    return DomainRegistration(
        tools=(send_feishu_summary,),
        risk={},
        egress=(_EGRESS_FEISHU_WEBHOOK,),
    )
