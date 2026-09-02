"""Web 终端后端属主:引擎/队列/心跳/事件缓存/审计旁路装配在服务进程内。

启动 web 服务进程即成为唯一属主:SessionEngine(thread_id 参数化)、
单 worker 队列(操作员回合与心跳系统消息共用,与 TUI 侧同一形态)、
pacemaker 心跳循环全部进程内运行。浏览器是客户端;远期 TUI 亦改造为
客户端,事件缓存按客户端协议的下行信封形状({seq, type, origin,
payload},Q16)存事件,浏览器特有物不进缓存。

部件边界:
- EventCache:回合事件的有界环形缓冲,seq 进程内单调——快照端点与
  断线补发(携 last_seq 取增量)共用的数据面
- history_events_from_messages:重启历史粗重建的纯映射(Q15)——存档
  消息 → 消息级事件;缓存空时由属主在启动期播回缓存,REST 快照与
  WS 重放两条读取路径即见历史,浏览器刷新不白屏
- AuditTap:log_event 只读订阅者的进程内缓冲,审计条目经 REST 查询;
  JSONL 写路径零改动,文件保持纯档案职责(Q14)
- BackendOwner:装配与生命周期——start 挂旁路、播历史(缓存空时)、
  起 worker 与 pacemaker;stop 撤旁路、收任务、关资源。回合异常落缓存
  为 turn_error 事件,不杀 worker(单 worker 不被一条坏回合堵死)。
  事件落缓存即向在线 WS 连接广播(subscribe/unsubscribe,见 entry.web_ws)

审计落点由调用方先行 init_audit_logger(属主只订阅,不初始化)。
审批动线(07 票):装配工厂造一只 WebApprovalBridge,同一只接两侧——
engine 的 approval_responder 与属主的 answer_approval;decision 帧经
entry.web_ws 落到属主桥。不答即拒由引擎超时兜底(TIMEOUT 留痕);审批帧
payload 附引擎真实超时(timeout_seconds),卡面倒计时以此为限。
"""
import asyncio
import itertools
import logging
import os
import threading
from collections import deque
from contextlib import AsyncExitStack
from dataclasses import dataclass
from difflib import unified_diff
from typing import Awaitable, Callable, Iterable, Mapping, Optional

from langchain_core.messages import AnyMessage

from auditronclaw.core.bus import TurnRequest
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.heartbeat import pacemaker_loop
from auditronclaw.core.logger import get_audit_logger
from auditronclaw.core.approval.gate import ApprovalDecision, TurnOrigin
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    SessionEngine,
    ToolCall,
    ToolResult,
    TurnEnd,
    TurnEvent,
    TurnTrajectory,
)
from auditronclaw.core.tools.sandbox_tools import _resolve_office_path
from entry.web_approval import WebApprovalBridge

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


# ============ write 审批复预览:统一 diff 行(payload 可选字段,12 票) ============
#
# 审批门批的是改动本身:write_office_file 的审批帧带新旧内容的统一 diff
# 行数组({t: ctx|add|del|h, text 含前缀字符}),前端按行分色呈现。
# 读旧文件是纯读(写尚未发生);任何提不出可信输入的场合返回 None,
# payload 不带 diff,前端回落整段内容预览(fail-closed,与执行判定同向)。

_DIFF_LINE_KINDS = {" ": "ctx", "+": "add", "-": "del"}


def build_write_diff(office_dir: str, args: Mapping) -> "Optional[tuple[list, str]]":
    """write_office_file 审批预览的统一 diff 行数组(纯读,不执行写)。

    返回 (行数组, 归一化相对路径);filepath/content 形状不符、mode 非法、
    路径越界、office_dir 未装配或新旧无差异时返回 None(调用方回落整段
    内容预览)。路径解析与执行同源(_resolve_office_path,office 根基准化
    + 越界拦截);追加模式按工具的落盘形态补换行(防粘连的那一枚)。
    """
    if not office_dir:
        return None
    filepath, content = args.get("filepath"), args.get("content")
    if not isinstance(filepath, str) or not filepath or not isinstance(content, str):
        return None
    mode = args.get("mode", "w")
    if mode not in ("w", "a"):
        return None
    try:
        target_path, normalized = _resolve_office_path(office_dir, filepath)
    except PermissionError:
        return None
    old = ""
    if os.path.exists(target_path):
        with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
            old = f.read()
    if mode == "a":
        separator = "" if not content or content.startswith("\n") else "\n"
        new = old + separator + content
    else:
        new = content
    lines = list(unified_diff(old.splitlines(), new.splitlines(), lineterm=""))
    if not lines:
        return None
    rows: list = []
    for index, line in enumerate(lines):
        # 头两行是 unified_diff 的文件头(from/to 未具名,'--- '/'+++ ');
        # 之后的同前缀行是真删除/真内容,原样保留
        if index < 2 and (line.startswith("---") or line.startswith("+++")):
            continue
        kind = "h" if line.startswith("@@") else _DIFF_LINE_KINDS.get(line[:1], "ctx")
        rows.append({"t": kind, "text": line})
    return rows, normalized.replace("\\", "/")


# ============ 重启历史重建:checkpointer 存档 → 消息级事件 ============
#
# 后端进程重启后事件缓存为空,浏览器重连即白屏(Q15)。会话的持久事实
# 在 checkpointer 的消息存档里——从这里做消息级粗重建播回缓存。

HISTORY_ORIGIN = "history"
"""重建事件的 origin 标记:与实时流(human/heartbeat/…)可区分,客户端
据此把历史段与实时段分开呈现。存档里查不到各回合的来源(心跳与操作员
在消息上同形),重建不做来源猜测,一律标 history。"""


def history_events_from_messages(messages: "Iterable[AnyMessage]") -> list[TurnEvent]:
    """存档消息 → 回合事件序列:与实时流同一事件类型(经 serialize_turn_event
    落成同一信封形状)的消息级粗重建。

    与实时流的差别(不逐字复刻):审批过程事件(approval_request)不在
    存档里,自然不重建;回合输入文本不单独成事件(实时流本就没有这一型)。
    HumanMessage 分段回合,段尾补 turn_end——轨迹聚合与实时流同形,重启
    时跑到一半的回合按存档现状收口(该回合不会续跑,turn_end 如实交代
    已发生的轨迹)。
    """
    events: list[TurnEvent] = []
    turn_events: list[TurnEvent] = []
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    reply_text: list[str] = []

    def close_turn() -> None:
        nonlocal turn_events, tool_calls, tool_results, reply_text
        if turn_events:
            events.extend(turn_events)
            events.append(TurnEnd(trajectory=TurnTrajectory(
                tool_calls=tool_calls,
                tool_results=tool_results,
                reply="\n".join(reply_text),
            )))
        turn_events, tool_calls, tool_results, reply_text = [], [], [], []

    for msg in messages:
        kind = getattr(msg, "type", "")
        if kind == "human":
            close_turn()  # 存档里的回合边界(操作员输入与心跳系统消息同形)
        elif kind == "ai":
            calls = getattr(msg, "tool_calls", None) or []
            for tc in calls:
                args = tc.get("args", {})
                turn_events.append(ToolCall(name=tc["name"], args=args))
                tool_calls.append({"tool": tc["name"], "args": args})
            if msg.content:  # 与引擎 reply 语义同构:content 真值即发,final=无调用
                turn_events.append(Reply(content=str(msg.content),
                                         final=not calls))
                reply_text.append(str(msg.content))
        elif kind == "tool":
            result = str(msg.content)
            turn_events.append(ToolResult(tool=msg.name, result=result))
            tool_results.append({"tool": msg.name, "result": result})
        # 其余类型(system 等按理不进存档):跳过,不虚构事件
    close_turn()
    return events


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
                 approval: Optional[WebApprovalBridge] = None,
                 resources: Optional[AsyncExitStack] = None,
                 office_dir: str = ""):
        self.engine = engine
        self.tasks_file = tasks_file
        self.check_interval = check_interval
        self.cache = EventCache(maxlen=cache_size)
        self.audit_tap = AuditTap(maxlen=audit_buffer)
        # 审批桥:engine 构造时注入 responder 的那只必须与属主暴露的是同一只
        # (真实装配经 approval 参数显式传入;直构缺省自建——此时引擎没有
        # 应答通道,decision 帧永远无物可答,与未装配同形)
        self.approval = approval or WebApprovalBridge()
        # office 沙盒根:write 审批复预览的 diff 取旧文件用(纯读);
        # 未装配(直构/测试)不产 diff,payload 不带字段,前端回落整段预览
        self.office_dir = office_dir
        self.queue: "asyncio.Queue[TurnRequest | str]" = asyncio.Queue()
        self._resources = resources
        self._tasks: list[asyncio.Task] = []
        self._subscribers: "set[asyncio.Queue[CachedEvent]]" = set()
        self._unsubscribe_audit: Optional[Callable[[], None]] = None

    def answer_approval(self, decision: ApprovalDecision) -> bool:
        """WS decision 帧的落点:回填挂起审批,引擎同回合续行。

        False = 无挂起或已终局(超时已拒/已应答)——由 WS 层回
        decision_unavailable,答案弃置。
        """
        return self.approval.answer(decision)

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
        """起属主:先挂审计旁路(不漏事件),空缓存播入重启历史(次序先于
        实时事件),再起 worker 与 pacemaker。"""
        self._unsubscribe_audit = get_audit_logger().add_subscriber(self.audit_tap)
        await self._seed_history()
        self._tasks = [
            asyncio.create_task(self._worker_loop()),
            asyncio.create_task(pacemaker_loop(
                task_queue=self.queue, tasks_file=self.tasks_file,
                check_interval=self.check_interval)),
        ]

    async def _seed_history(self) -> None:
        """重启历史粗重建(Q15):缓存空时把存档消息历史播入缓存——快照
        端点与 WS 重放共用缓存,浏览器刷新即见。取数经引擎的
        archived_messages(会话存档的读取面,见 core.session)。

        快路径:缓存命中(非空)即跳过——重建只在启动期、缓存为空时至多
        一次,历史事件先于一切实时事件落缓存,两段次序天然分明。重建
        失败不杀启动:告警后以空历史续起(白屏可忍,服务不可用不可忍)。
        """
        if self.cache.snapshot():
            return
        read_archive = getattr(self.engine, "archived_messages", None)
        if read_archive is None:
            return
        try:
            messages = await read_archive()
        except Exception as exc:
            logger.warning("重启历史重建失败(以空历史续起): %r", exc)
            return
        for event in history_events_from_messages(messages):
            type_, payload = serialize_turn_event(event)
            self.cache.append(type_, HISTORY_ORIGIN, payload)

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
                    if type_ == "approval_request":
                        # 审批帧附引擎真实超时(Q10:AUDITRONCLAW_APPROVAL_TIMEOUT
                        # 构造期可配):不答即拒的期限在引擎,卡面倒计时以此为限
                        payload["timeout_seconds"] = self.engine.approval_timeout
                        # write 类文件写入附统一 diff 预览(12 票):操作员批的
                        # 是改动本身;提不出可信 diff 时 payload 不带字段,
                        # 前端回落整段内容预览
                        if event.tool == "write_office_file" and self.office_dir:
                            preview = build_write_diff(self.office_dir, event.args)
                            if preview is not None:
                                payload["diff"], payload["filename"] = preview
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
    审批桥同一只接两侧(07 票):engine 的应答通道与属主的 decision 帧
    落点——人来源回合的必批调用经 WS 问人,不答即拒由引擎超时兜底。
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
        bridge = WebApprovalBridge()
        engine = SessionEngine(app, thread_id, approval_responder=bridge.responder)
        return BackendOwner(engine=engine, tasks_file=workspace.tasks_file,
                            check_interval=check_interval, approval=bridge,
                            resources=resources, office_dir=workspace.office_dir)

    return _factory
