"""会话引擎(session):以会话为持有域、以回合为操作粒度的深模块。

run_turn(text) 驱动一个回合:内部跑 app.astream(updates 流),把底层
消息更新解析为回合事件逐个发出,收尾发 TurnEnd 搭载聚合好的回合轨迹。
TUI、基准、Web 终端都是它的适配器,线格式翻译与展示归消费者。

职责边界(词汇见 CONTEXT.md:会话引擎/回合/回合事件/回合轨迹):
- 引擎只含 astream 驱动 + 事件解析 + 回合轨迹收集
- 不吃输入队列(回合级;队列循环留在 TUI worker)、不管心跳(pacemaker
  继续塞 task_queue)、不做审计埋点(留在 agent_node,不动 LangGraph 图)
- 不 import agent 模块:app 由调用方构造后注入,基准 reload 链零牵动

reply 语义(与基准 _drive_agent 旧手写解析逐字段等价,等价性由
tests/test_session_engine.py 把守):所有带文本的非工具消息都发 Reply,
final=True 当且仅当该消息无 tool_calls;content 与 tool_calls 并存时
两者都发。ToolResult 带完整结果文本,截断归消费者。回合内异常原样
上抛、不发 TurnEnd;展示与记账由适配器决定。
"""

from dataclasses import dataclass
from typing import AsyncIterator, List

from langchain_core.messages import HumanMessage


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
    """审批打断事件:仅接口声明占位,产生与续行属策略层(第三章)。"""


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


class SessionEngine:
    """会话引擎:构造时绑定 app 与 thread_id,run_turn 跑一个回合。"""

    def __init__(self, app, thread_id: str):
        self.app = app
        self.thread_id = thread_id

    async def run_turn(self, text: str) -> AsyncIterator[TurnEvent]:
        """驱动一个回合:astream → 回合事件流,收尾发 TurnEnd 搭载轨迹。

        输入即纯文本:HumanMessage 构造由引擎负责(心跳系统消息同走此口)。
        configurable.thread_id 的 config 构造收在引擎内。回合内异常原样
        上抛,不发 TurnEnd。
        """
        config = {"configurable": {"thread_id": self.thread_id}}
        inputs = {"messages": [HumanMessage(content=text)]}

        tool_calls: List[dict] = []
        tool_results: List[dict] = []
        reply_text: List[str] = []

        async for event in self.app.astream(inputs, config=config, stream_mode="updates"):
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

        yield TurnEnd(trajectory=TurnTrajectory(
            tool_calls=tool_calls,
            tool_results=tool_results,
            reply="\n".join(reply_text),
        ))
