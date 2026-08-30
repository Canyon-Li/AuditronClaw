"""会话引擎(session):以会话为持有域、以回合为操作粒度的深模块。

run_turn(text, origin) 驱动一个回合:内部跑 app.astream(updates 流),把底层
消息更新解析为回合事件逐个发出,收尾发 TurnEnd 搭载聚合好的回合轨迹。
TUI、基准、Web 终端都是它的适配器,线格式翻译与展示归消费者。

职责边界(词汇见 CONTEXT.md:会话引擎/回合/回合事件/回合轨迹):
- 引擎只含 astream 驱动 + 事件解析 + 回合轨迹收集 + 审批打断-续行
- 不吃输入队列(回合级;队列循环留在 TUI worker)、不管心跳(pacemaker
  继续塞 task_queue)、不做审计埋点(留在 agent_node 与审批门,不动 LangGraph 图)
- 不 import agent 模块:app 由调用方构造后注入,基准 reload 链零牵动

回合来源(03 票):origin 类型化传入(TurnOrigin),缺省 unattended——
来源不声明的调用方(基准、既有消费者)自动落在无人形态,门构造上不问人。
只有 human 来源会出现审批打断。

审批打断-续行(03 票):门在工具节点处 LangGraph interrupt,引擎把打断
载荷重组为 ApprovalRequest 回合事件发出,经应答通道取回 ApprovalDecision,
超时(默认 5 分钟可配)即按拒绝续行,再以 Command resume 续跑同回合。
应答通道(approval_responder)由交互式适配器注入(TUI/Web);没有应答通道
时人来源回合也立即按无人拒。审批留痕在门,引擎只传递决定。

reply 语义(与基准 _drive_agent 旧手写解析逐字段等价,等价性由
tests/test_session_engine.py 把守):所有带文本的非工具消息都发 Reply,
final=True 当且仅当该消息无 tool_calls;content 与 tool_calls 并存时
两者都发。ToolResult 带完整结果文本,截断归消费者。回合内异常原样
上抛、不发 TurnEnd;展示与记账由适配器决定。
"""

import asyncio
import inspect
import os
from contextlib import aclosing
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, List, Optional, Union

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .approval.gate import (
    ApprovalDecision,
    DecisionSource,
    TurnOrigin,
    ensure_decision,
)
# ============ 审批等待超时 ============
#
# 默认 5 分钟:挂起的审批到期即终局拒绝——单 worker 队列不被一条挂起审批
# 堵死;超时即终局,不排队等下一个应答。环境变量在引擎构造期读取
# (装配期取值,不冻结于 import 期)。

_APPROVAL_TIMEOUT_ENV = "AUDITRONCLAW_APPROVAL_TIMEOUT"
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300.0


def default_approval_timeout() -> float:
    """构造期读取的审批超时默认(环境变量可配)。"""
    return float(os.getenv(_APPROVAL_TIMEOUT_ENV,
                           str(DEFAULT_APPROVAL_TIMEOUT_SECONDS)))


# ============ 回合事件 ============

@dataclass(frozen=True)
class TurnEvent:
    """回合事件基类:引擎对外发出的所有事件的共同祖先。"""


@dataclass(frozen=True)
class ToolCall(TurnEvent):
    name: str
    args: dict


@dataclass(frozen=True)
class ToolResult(TurnEvent):
    tool: str
    result: str  # 完整结果文本,不截断(截断归消费者)


@dataclass(frozen=True)
class Reply(TurnEvent):
    content: str
    final: bool  # True 当且仅当该消息无 tool_calls(回合就此收尾)


@dataclass(frozen=True)
class ApprovalRequest(TurnEvent):
    """审批打断事件:人来源回合的必批调用经门发出,应答后续行。

    字段与审批门的 interrupt 载荷同形:tool/args 是 schema 规范化后的完整
    调用(人批的就是将要执行的那份参数),risk_class/reason 来自副作用
    分级。答案是 ApprovalDecision(approval/persist/source)。
    """
    tool: str
    args: dict
    risk_class: str
    reason: str


@dataclass(frozen=True)
class TurnTrajectory:
    """回合轨迹:tool_calls / tool_results / reply 有序记录,形状与基准
    _drive_agent 现收集结果逐字段等价(基准判定与 Reflector 的事实载体)。"""

    tool_calls: List[dict]    # [{"tool", "args"}] 按消息序
    tool_results: List[dict]  # [{"tool", "result"}] 与 tool_calls 按序对应
    reply: str                # "\n".join(全部 reply 文本)


@dataclass(frozen=True)
class TurnEnd(TurnEvent):
    trajectory: TurnTrajectory


# 应答通道:同步函数或协程,收到 ApprovalRequest 返回 ApprovalDecision。
# 引擎侧统一 ensure_decision 校验(不合规按无人拒)、异步路径统一超时。
ApprovalResponder = Callable[[ApprovalRequest], Union[ApprovalDecision, Awaitable[ApprovalDecision]]]


class SessionEngine:
    """会话引擎:构造时绑定 app 与 thread_id,run_turn 跑一个回合。"""

    def __init__(self, app, thread_id: str,
                 approval_responder: Optional[ApprovalResponder] = None,
                 approval_timeout: Optional[float] = None):
        self.app = app
        self.thread_id = thread_id
        self.approval_responder = approval_responder
        # None = 构造期读环境默认(装配期取值);显式注入供测试与特殊形态
        self.approval_timeout = (approval_timeout if approval_timeout is not None
                                 else default_approval_timeout())

    async def _await_decision(self, request: ApprovalRequest) -> ApprovalDecision:
        """取审批应答:无通道立即拒;异步应答带超时;异常/垃圾值 fail-closed。"""
        if self.approval_responder is None:
            return ApprovalDecision(approved=False, persist=False,
                                    source=DecisionSource.UNATTENDED)
        try:
            outcome = self.approval_responder(request)
            if inspect.isawaitable(outcome):
                decision = await asyncio.wait_for(outcome,
                                                  timeout=self.approval_timeout)
            else:
                decision = outcome
        except asyncio.TimeoutError:
            return ApprovalDecision(approved=False, persist=False,
                                    source=DecisionSource.TIMEOUT)
        except Exception:
            # 应答通道故障≠挂起整个回合:按无人拒收尾,单 worker 队列不堵
            return ApprovalDecision(approved=False, persist=False,
                                    source=DecisionSource.UNATTENDED)
        return ensure_decision(decision)

    async def run_turn(self, text: str,
                       origin: TurnOrigin = TurnOrigin.UNATTENDED
                       ) -> AsyncIterator[TurnEvent]:
        """驱动一个回合:astream → 回合事件流,收尾发 TurnEnd 搭载轨迹。

        输入即纯文本:HumanMessage 构造由引擎负责(心跳系统消息同走此口)。
        origin 类型化标记回合来源,经 config.configurable 传给审批门——
        缺省 unattended,不声明来源的调用方构造上永不触发审批打断。
        人来源回合遇到审批打断:发 ApprovalRequest,取回应答(超时即拒)
        后 Command resume 续跑,直至回合自然收束。回合内异常原样上抛,
        不发 TurnEnd。
        """
        config = {"configurable": {"thread_id": self.thread_id,
                                   "turn_origin": origin.value}}
        inputs: dict | Command = {"messages": [HumanMessage(content=text)]}

        tool_calls: List[dict] = []
        tool_results: List[dict] = []
        reply_text: List[str] = []

        while True:
            approval_payload = None
            # aclosing:遇打断 break 后确定性关闭底层流,不靠 GC 收尾
            async with aclosing(self.app.astream(
                    inputs, config=config, stream_mode="updates")) as stream:
                async for event in stream:
                    if "__interrupt__" in event:
                        # 审批打断(updates 流的专用键):载荷是门发出的
                        # ApprovalRequest 字段形状。同批多个必批调用时逐个
                        # 打断、逐个应答(其余打断在 resume 后依次浮现)
                        approval_payload = event["__interrupt__"][0].value
                        break
                    for _node, node_data in event.items():
                        for msg in node_data.get("messages", []):
                            calls = getattr(msg, "tool_calls", None)
                            if calls:
                                for tc in calls:
                                    args = tc.get("args", {})
                                    tool_calls.append({"tool": tc["name"], "args": args})
                                    yield ToolCall(name=tc["name"], args=args)
                            if getattr(msg, "type", "") == "tool":
                                tool_results.append({"tool": msg.name, "result": str(msg.content)})
                                yield ToolResult(tool=msg.name, result=str(msg.content))
                            elif msg.content:
                                reply_text.append(str(msg.content))
                                yield Reply(content=str(msg.content), final=not calls)

            if approval_payload is None:
                break
            request = ApprovalRequest(**approval_payload)
            yield request
            decision = await self._await_decision(request)
            inputs = Command(resume=decision)

        yield TurnEnd(trajectory=TurnTrajectory(
            tool_calls=tool_calls,
            tool_results=tool_results,
            reply="\n".join(reply_text),
        ))
