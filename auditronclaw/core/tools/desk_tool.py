from datetime import datetime, timedelta
from typing import List, Optional

from pydantic import BaseModel, Field

from .base import auditronclaw_tool
from ..logger import audit_logger

# ============ 事务台结构化提交工具 ============
#
# 弱模型真实运行结论(2026-08-23):格式、顺序、副作用靠自然语言指令约束不住——
# 模型抄指令速记当标题、跳过落盘步、在对话里谎报"已推送已存入"。本工具把
# 控制面移进 function calling:模型只填 schema 字段(分类判断),「分类账」
# 渲染、待办落盘、飞书推送全部代码化,顺序写死——先落盘后推送,"推送失败
# 不吞待办"从提示词约定变成代码顺序。管线从 3 次工具调用降到 2 次
# (read_recent_emails → submit),落在弱模型实测可靠的串联区间。


class DeskTodo(BaseModel):
    item: str = Field(description="待办事项,一句话说清要做什么")
    source: str = Field(description="来源:发件人或邮件主题")
    deadline: Optional[str] = Field(
        default=None, description="截止日,格式 YYYY-MM-DD;邮件里没有就不填"
    )


class DeskMailLine(BaseModel):
    sender: str = Field(description="发件人")
    subject: str = Field(description="邮件主题")


class DeskReport(BaseModel):
    window_hours: int = Field(description="读取窗口(小时),与 read_recent_emails 用的窗口一致,如 24")
    total_mails: int = Field(description="窗口内邮件总数")
    todos: List[DeskTodo] = Field(
        default_factory=list,
        description="跨类别提炼的待办列表(没有待办就传空列表)",
    )
    needs_reply: List[DeskMailLine] = Field(
        default_factory=list, description="需要对方回话的邮件"
    )
    notices: List[DeskMailLine] = Field(
        default_factory=list, description="通知类邮件(不含待办、不需回话)"
    )
    ignorable_count: int = Field(
        default=0, description="可忽略(纯促销/群发)邮件数"
    )
    ignorable_top_senders: List[str] = Field(
        default_factory=list, description="可忽略邮件的主要发件人,前几名即可"
    )


def render_desk_report_text(report: DeskReport) -> str:
    """
    「分类账」渲染(确定性):模型只提供数据,格式由代码钉死。

    计头 + 四段(■ 待办 / ■ 需回复 / ■ 通知 / ■ 可忽略),段标题是字面常量,
    展开密度随类别递减——待办逐项带来源与截止,可忽略只留计数与发件人前几名。
    """
    lines = [
        f"邮箱事务台日报 | 窗口{report.window_hours}小时 | "
        f"共{report.total_mails}封 · 跨类别待办 {len(report.todos)} 项",
        "",
        "■ 待办",
    ]
    lines += [f"{t.item} | {t.source} | {t.deadline or '无截止'}" for t in report.todos] or ["无"]
    lines += ["", "■ 需回复"]
    lines += [f"{m.sender} | {m.subject}" for m in report.needs_reply] or ["无"]
    lines += ["", "■ 通知"]
    lines += [f"{m.sender} | {m.subject}" for m in report.notices] or ["无"]
    lines += ["", "■ 可忽略"]
    if report.ignorable_count:
        tops = "、".join(report.ignorable_top_senders)
        lines.append(f"共{report.ignorable_count}封" + (f"({tops})" if tops else ""))
    else:
        lines.append("无")
    return "\n".join(lines)


def _target_time_for(deadline: Optional[str]) -> str:
    """
    截止日 → target_time:日期取当天 09:00;缺失/格式错/已过期 → 明天 09:00。

    时间换算是代码的事——真实运行中弱模型多次把过期日期原样传入被工具拒绝,
    这类换算不交给模型。
    """
    fallback = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d") + " 09:00:00"
    if not deadline:
        return fallback
    try:
        day = datetime.strptime(deadline.strip(), "%Y-%m-%d")
    except ValueError:
        return fallback
    target = day.replace(hour=9, minute=0, second=0)
    if target <= datetime.now():
        return fallback
    return target.strftime("%Y-%m-%d %H:%M:%S")


@auditronclaw_tool(args_schema=DeskReport)
def submit_mailbox_desk_report(
    window_hours: int,
    total_mails: int,
    todos: list = None,
    needs_reply: list = None,
    notices: list = None,
    ignorable_count: int = 0,
    ignorable_top_senders: list = None,
) -> str:
    """
    提交邮箱事务台日报：把 read_recent_emails 读到的邮件的分类结果作为结构化参数
    一次性提交。工具内部完成「分类账」渲染、待办落任务列表、飞书推送——你只负责
    分类判断，不要自己排版日报文本，也不要再调用 send_feishu_summary 或
    schedule_task 完成本轮事务台的工作。

    【空收件箱】：窗口内一封邮件也没有，同样必须提交空日报（total_mails=0、
    各列表传空）。空日报是事务台每日运行的存活信号——飞书收不到日报，说明的
    不是"今天没邮件"，而是"管线没在跑"；不要以没有邮件为由跳过提交。

    【分类规则】：
    1. 待办是跨类别正交维度：性质上属于"通知"的邮件（账单、续费提醒）同样携带
       待办，不要因为它是通知就漏掉待办；
    2. 需要对方回话的邮件归 needs_reply（朋友来信、等你答复的询问）；
    3. 纯促销/群发广告归可忽略（只报数量与主要发件人，不逐封列）；
    4. 其余告知性邮件归 notices。

    【边界（代码强制，非约定）】：
    1. 推送目标域名固定为飞书 webhook 域，经域名白名单守卫校验——参数面里
       没有 URL 字段；
    2. 待办先落盘、后推送：推送失败时待办已安全落在任务列表里；
    3. deadline 缺失、格式错误或已过期时，工具自动设为明天 09:00，不需要你算时间；
    4. webhook 等凭据只存在宿主机 .env，不会出现在参数、返回值或审计日志中。
    """
    try:
        report = DeskReport(
            window_hours=window_hours,
            total_mails=total_mails,
            todos=todos or [],
            needs_reply=needs_reply or [],
            notices=notices or [],
            ignorable_count=ignorable_count,
            ignorable_top_senders=ignorable_top_senders or [],
        )

        # 1. 落待办（先于推送——顺序即"推送失败不吞待办"的保障）。
        # 惰性导入：注册表在 builtins，模块级互引会成环。
        from .builtins import _append_task
        for t in report.todos:
            desc = f"{t.item} | {t.source}" + (f" | 截止 {t.deadline}" if t.deadline else " | 无截止")
            _append_task(_target_time_for(t.deadline), desc)
        if report.todos:
            audit_logger.log_event(
                thread_id="system",
                event="system_action",
                content=(
                    f"事务台待办落盘：{len(report.todos)} 项"
                    "（提交工具 submit_mailbox_desk_report）。"
                ),
            )

        # 2. 推送（共用飞书核心路径：同一注入点、同一道域名门、同一套审计）
        from . import feishu_tool
        text = render_desk_report_text(report)
        pushed, push_message = feishu_tool.push_text_via_bound_domain(
            text, tool_name=submit_mailbox_desk_report.name
        )

        # 3. 结构化回执：落盘数与推送结果分开陈述，成功失败都如实
        if pushed:
            return (
                f"✅ 事务台本轮完成：待办 {len(report.todos)} 项已落任务列表。"
                f"{push_message}"
            )
        return (
            f"⚠️ 事务台本轮部分完成：待办 {len(report.todos)} 项已落任务列表，"
            f"但推送未成功。{push_message}"
        )
    except Exception as e:
        # 结构化错误兜底：只报错误类型，不透传 str(e)（可能内嵌凭据）
        error_name = type(e).__name__
        audit_logger.log_event(
            thread_id="system",
            event="system_action",
            content=f"事务台日报提交失败：错误 {error_name}。",
        )
        return f"❌ 事务台日报提交失败（{error_name}）。待办与推送均未完成，请检查后重试。"
