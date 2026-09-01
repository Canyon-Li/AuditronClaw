"""Web 终端后端属主:引擎/队列/心跳/事件缓存/审计旁路装配在服务进程内。

启动 web 服务进程即成为唯一属主:SessionEngine(thread_id 参数化)、
单 worker 队列(操作员回合与心跳系统消息共用,与 TUI 侧同一形态)、
pacemaker 心跳循环全部进程内运行。浏览器是客户端;远期 TUI 亦改造为
客户端,事件缓存按客户端协议的下行信封形状({seq, type, origin,
payload},Q16)存事件,浏览器特有物不进缓存。

部件边界:
- EventCache:回合事件的有界环形缓冲,seq 进程内单调——快照端点与
  断线补发(携 last_seq 取增量)共用的数据面
- AuditTap:log_event 只读订阅者的进程内缓冲,审计条目经 REST 查询;
  JSONL 写路径零改动,文件保持纯档案职责(Q14)
- BackendOwner:装配与生命周期——start 挂旁路、起 worker 与 pacemaker;
  stop 撤旁路、收任务、关资源。回合异常落缓存为 turn_error 事件,
  不杀 worker(单 worker 不被一条坏回合堵死)。事件落缓存即向在线
  WS 连接广播(subscribe/unsubscribe,见 entry.web_ws)

审计落点由调用方先行 init_audit_logger(属主只订阅,不初始化)。
审批应答通道(07 票接线前)不注入:人来源回合的必批调用按无人拒。
"""
import asyncio
import itertools
import logging
import threading
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from auditronclaw.core.bus import TurnRequest
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.heartbeat import pacemaker_loop
from auditronclaw.core.logger import get_audit_logger
from auditronclaw.core.approval.gate import TurnOrigin
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    SessionEngine,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_CACHE_SIZE = 1000
DEFAULT_AUDIT_BUFFER = 1000
DEFAULT_CHECK_INTERVAL = 10.0
# 单连接实时队列上界:慢消费者丢帧不堵 worker,客户端凭 seq 校验发现
# 缺口后携 last_seq=0 整段重建(契约见 entry.web_ws)
SUBSCRIBER_QUEUE_MAX = 256


# ============ 事件缓存:有界环形缓冲,seq 单调 ============

@dataclass(frozen=True)
class CachedEvent:
    """下行信封(Q16):{seq, type, origin, payload}。

    type 是五种回合事件之一(或回合异常的 turn_error);origin 来自
    TurnOrigin 的值(human / heartbeat / …),客户端据此过滤心跳回合。
    """

    seq: int
    type: str
    origin: str
    payload: dict


def serialize_turn_event(event: TurnEvent) -> tuple[str, dict]:
    """回合事件 → (type, payload):引擎 dataclass 到客户端协议的翻译单点。"""
    if isinstance(event, ToolCall):
        return "tool_call", {"name": event.name, "args": event.args}
    if isinstance(event, ToolResult):
        return "tool_result", {"tool": event.tool, "result": event.result}
    if isinstance(event, Reply):
        return "reply", {"content": event.content, "final": event.final}
    if isinstance(event, ApprovalRequest):
        return "approval_request", {
            "tool": event.tool, "args": event.args,
            "risk_class": event.risk_class, "reason": event.reason,
        }
    if isinstance(event, TurnEnd):
        trajectory = event.trajectory
        return "turn_end", {
            "tool_calls": trajectory.tool_calls,
            "tool_results": trajectory.tool_results,
            "reply": trajectory.reply,
        }
    raise TypeError(f"未知回合事件类型: {type(event).__name__}")


class EventCache:
    """回合事件环形缓冲:append 与 snapshot 可能来自不同线程,锁短持有。"""

    def __init__(self, maxlen: int = DEFAULT_CACHE_SIZE):
        self._events: "deque[CachedEvent]" = deque(maxlen=maxlen)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def append(self, type_: str, origin: str, payload: dict) -> CachedEvent:
        with self._lock:
            event = CachedEvent(seq=next(self._seq), type=type_,
                                origin=origin, payload=payload)
            self._events.append(event)
        return event

    def snapshot(self, since: int = 0) -> list[CachedEvent]:
        """seq > since 的存量事件(缺省全量);环形溢出丢最旧,seq 不回卷。"""
        with self._lock:
            return [e for e in self._events if e.seq > since]

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._events[-1].seq if self._events else 0


# ============ 审计旁路缓冲:条目可查询 ============

class AuditTap:
    """log_event 订阅者的进程内缓冲:seq 自编号,支持增量与过滤查询。"""

    def __init__(self, maxlen: int = DEFAULT_AUDIT_BUFFER):
        self._entries: "deque[dict]" = deque(maxlen=maxlen)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def __call__(self, entry: dict) -> None:
        with self._lock:
            record = {"seq": next(self._seq), **entry}
            self._entries.append(record)

    def query(self, *, limit: int = 50, thread_id: Optional[str] = None,
              event: Optional[str] = None, since: int = 0) -> list[dict]:
        """旁路条目查询:取过滤后的最新 limit 条(时序仍从旧到新),seq > since 为增量。"""
        with self._lock:
            entries = list(self._entries)
        rows = [e for e in entries
                if e["seq"] > since
                and (thread_id is None or e.get("thread_id") == thread_id)
                and (event is None or e.get("event") == event)]
        if limit <= 0:
            return []
        return rows[-limit:]


# ============ 属主:装配与生命周期 ============

class BackendOwner:
    """唯一属主:一个 SessionEngine + 一个队列 + 一个 worker + 一颗心脏。"""

    def __init__(self, *, engine: SessionEngine, tasks_file: str,
                 check_interval: float = DEFAULT_CHECK_INTERVAL,
                 cache_size: int = DEFAULT_CACHE_SIZE,
                 audit_buffer: int = DEFAULT_AUDIT_BUFFER,
                 resources: Optional[AsyncExitStack] = None):
        self.engine = engine
        self.tasks_file = tasks_file
        self.check_interval = check_interval
        self.cache = EventCache(maxlen=cache_size)
        self.audit_tap = AuditTap(maxlen=audit_buffer)
        self.queue: "asyncio.Queue[TurnRequest | str]" = asyncio.Queue()
        self._resources = resources
        self._tasks: list[asyncio.Task] = []
        self._subscribers: "set[asyncio.Queue[CachedEvent]]" = set()
        self._unsubscribe_audit: Optional[Callable[[], None]] = None

    # ============ 实时广播:事件落缓存即扇出到在线 WS 连接 ============

    def subscribe(self) -> "asyncio.Queue[CachedEvent]":
        """注册实时订阅(WS 连接建立时调用):此后的新事件即时入队。

        连接即补发的次序约定:先 subscribe 再取快照——快照与队列之间
        竞态窗口内的事件两处都有,消费端按 seq 去重(entry.web_ws)。
        """
        queue: "asyncio.Queue[CachedEvent]" = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_MAX)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[CachedEvent]") -> None:
        self._subscribers.discard(queue)

    def _broadcast(self, event: CachedEvent) -> None:
        """扇出至全部订阅队列:不阻塞 worker,满队列丢帧由客户端自愈。"""
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # 慢消费者:丢的是这一连接的实时性,不是缓存;客户端按
                # seq 校验发现缺口后携 last_seq=0 重连整段重建
                pass

    async def start(self) -> None:
        """起属主:先挂审计旁路(不漏事件),再起 worker 与 pacemaker。"""
        self._unsubscribe_audit = get_audit_logger().add_subscriber(self.audit_tap)
        self._tasks = [
            asyncio.create_task(self._worker_loop()),
            asyncio.create_task(pacemaker_loop(
                task_queue=self.queue, tasks_file=self.tasks_file,
                check_interval=self.check_interval)),
        ]

    async def stop(self) -> None:
        """收属主:撤旁路、收任务、关资源。幂等,未 start 时为空操作。"""
        if self._unsubscribe_audit is not None:
            self._unsubscribe_audit()
            self._unsubscribe_audit = None
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks = []
        if self._resources is not None:
            await self._resources.aclose()
            self._resources = None

    async def submit(self, text: str,
                     origin: TurnOrigin = TurnOrigin.HUMAN) -> None:
        """操作员回合入口:类型化信封入队,由唯一 worker 串行消费。"""
        await self.queue.put(TurnRequest(text=text, origin=origin))

    async def _worker_loop(self) -> None:
        """单 worker:队列逐项驱动引擎回合,事件流逐个落缓存。

        裸串(无来源声明)按无人值守消费——与 TUI worker 同构的
        fail-closed 形态。回合异常落 turn_error 事件并继续:队列不因
        一条坏回合停摆。
        """
        while True:
            item = await self.queue.get()
            if isinstance(item, TurnRequest):
                text, origin = item.text, item.origin
            else:
                text, origin = item, TurnOrigin.UNATTENDED
            try:
                async for event in self.engine.run_turn(text, origin=origin):
                    type_, payload = serialize_turn_event(event)
                    self._broadcast(self.cache.append(type_, origin.value, payload))
            except Exception as exc:
                # CancelledError 不在此列:取消即属主停机,原样上抛交 stop 收口
                logger.warning("回合异常(worker 继续运行): %r", exc)
                self._broadcast(self.cache.append(
                    "turn_error", origin.value,
                    {"error": f"{type(exc).__name__}: {exc}"}))
            finally:
                self.queue.task_done()


def assemble_backend_owner(*, thread_id: str, provider_name: str,
                           model_name: str, workspace: WorkspaceConfig,
                           check_interval: float = DEFAULT_CHECK_INTERVAL
                           ) -> Callable[[], Awaitable[BackendOwner]]:
    """生产装配工厂:调用方(入口)先 init_audit_logger,再取本工厂注入 app。

    返回 async 工厂——引擎的 checkpointer(AsyncSqliteSaver)是异步资源,
    在 FastAPI lifespan 的运行环里构造;stop 经 AsyncExitStack 统一收口。
    审批应答通道未注入(07 票接线),人来源回合必批调用现按无人拒。
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from auditronclaw.core.agent import create_agent_app

    async def _factory() -> BackendOwner:
        resources = AsyncExitStack()
        memory = await resources.enter_async_context(
            AsyncSqliteSaver.from_conn_string(workspace.db_path))
        app = create_agent_app(provider_name=provider_name,
                               model_name=model_name, workspace=workspace,
                               checkpointer=memory, thread_id=thread_id)
        engine = SessionEngine(app, thread_id)
        return BackendOwner(engine=engine, tasks_file=workspace.tasks_file,
                            check_interval=check_interval, resources=resources)

    return _factory
