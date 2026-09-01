"""Web 终端 WS 通道:浏览器(及远期 TUI)与属主之间的双向实时契约。

契约 v1(05 票定稿;客户端无关——信封不含浏览器特有载荷,契约即
事件缓存形状,REST 快照端点与 WS 下行同源):

端点
- /ws?token=<启动 token>&last_seq=<int>(token 复用 02 中间件,
  query 或 cookie 均可):无/错 token 拒握手 close 1008
- 属主未装配(脚手架形态):accept 后 close 1011(对照 REST 503)

下行信封 {seq, type, origin, payload}
- type:五种回合事件(tool_call/tool_result/reply/approval_request/
  turn_end)+ turn_error(回合异常)+ protocol_error(上行帧处理错误)
- 回合事件的 origin 取回合来源(human/heartbeat/bench/unattended),
  客户端按 origin 过滤——心跳回合主视图不展示
- protocol_error 由服务进程自身产生:seq=0(不与流事件冲突,流自 1 起)、
  origin="server",不入事件缓存
- seq:进程内单调、从 1 起,仅后端进程重启回卷;重启后的历史重建
  (checkpointer 消息级)归 06 票,重建事件同样自 seq 1 重新编号

连接语义(断线补发与刷新不丢画面)
- 连接即补发:先发缓存中 seq > last_seq 的事件再进实时段;last_seq
  缺省 0 即全量重放——刷新后的画面重建走这条路径
- 环形缓冲有界(默认 1000),溢出丢最旧:全量重放的起点可能大于 1,
  断线补发的起点也可能大于 last_seq+1。客户端 seq 规则据此分两段:
  · 连接首帧重设锚点:帧序大于 last_seq+1 是断线期间缓冲溢出,丢的
    窗口已不可找回,取帧序为新锚续播;帧序小于等于已见值是后端
    重启回卷,本地画面清空后取新锚重建
  · 连接内每帧应为上一帧 +1,断序即本连接丢帧(慢消费),携
    last_seq 断开重连、由缓存补缺口

上行帧(客户端 → 服务端,JSON 文本)
- {"type": "input", "text": str}
  入队成 human 回合(与心跳同队列串行消费);空/空白/非字符串按
  input_empty 拒
- {"type": "decision", "choice": "once" | "always" | "deny"}
  审批应答,07 票接线;当前无挂起审批可应答,回
  protocol_error(decision_unavailable)

错误码(protocol_error.payload.code)
- bad_frame             非 JSON / 非对象 / 缺 type
- unknown_type          type 不在 input | decision
- input_empty           input 帧无有效文本
- decision_unavailable  当前无挂起审批可应答

ping/pong
- 无应用层 ping:保活交给 WebSocket 协议层(uvicorn 默认 20s 间隔
  ping、20s pong 超时断开);浏览器自动应答协议 ping,客户端只需
  处理断线重连
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket

if TYPE_CHECKING:
    from entry.web_owner import BackendOwner, CachedEvent

logger = logging.getLogger(__name__)

# protocol_error 的信封字段:seq=0 不与流事件(自 1 起)冲突;origin=server
# 标记服务进程自身产生的帧(回合事件的 origin 来自回合来源枚举)
PROTOCOL_ERROR_SEQ = 0
PROTOCOL_ERROR_ORIGIN = "server"

# 上行帧处理错误码(契约表见模块 docstring;TS 侧镜像 web/src/protocol.ts)
CODE_BAD_FRAME = "bad_frame"
CODE_UNKNOWN_TYPE = "unknown_type"
CODE_INPUT_EMPTY = "input_empty"
CODE_DECISION_UNAVAILABLE = "decision_unavailable"

_NO_OWNER_CLOSE_CODE = 1011
_NO_OWNER_CLOSE_REASON = "owner not assembled"


def _log_pump_exit(task: "asyncio.Task") -> None:
    """泵任务的收尸回调:异常没被 await 的路径也要留下线索。"""
    if not task.cancelled() and task.exception() is not None:
        logger.debug("WS 泵退出:%r", task.exception())


def register_ws_route(app) -> None:
    """挂 /ws(在静态兜底挂载之前调用,API 路由先于 "/" 挂载命中)。"""

    @app.websocket("/ws")
    async def ws_terminal(websocket: WebSocket, last_seq: int = 0) -> None:
        owner: Optional[BackendOwner] = getattr(
            websocket.app.state, "owner", None)
        if owner is None:
            # 脚手架形态:握手放行后明确 close,不静默空转(对照 REST 503)
            await websocket.accept()
            await websocket.close(code=_NO_OWNER_CLOSE_CODE,
                                  reason=_NO_OWNER_CLOSE_REASON)
            return

        await websocket.accept()
        queue = owner.subscribe()
        receive_task = asyncio.create_task(_receive_loop(websocket, owner))
        forward_task = asyncio.create_task(
            _forward_loop(websocket, owner, queue, last_seq))
        for task in (receive_task, forward_task):
            task.add_done_callback(_log_pump_exit)
        try:
            # 双泵并行:任一泵退出(断开/异常)即整体收口
            await asyncio.wait({receive_task, forward_task},
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            # 收尾只做同步动作,不补 await:连接断开时本协程可能正被取消,
            # 再等一发会把取消重新抛进收尾(测试客户端撕连接的竞态实测
            # 会让应用以被取消态收场)。子任务已请求取消,事件环下一拍
            # 自行收尸,异常经收尸回调留痕。
            owner.unsubscribe(queue)
            for task in (receive_task, forward_task):
                task.cancel()


async def _forward_loop(websocket: WebSocket, owner: "BackendOwner",
                        queue: "asyncio.Queue[CachedEvent]",
                        last_seq: int) -> None:
    """下行泵:先补发缓存缺口,再转发实时广播。

    seq 去重吸收"订阅早于快照"竞态窗口内的重复事件(见
    BackendOwner.subscribe 的次序约定);发送失败(断开)即退出,
    由外层收口清理。
    """
    sent = last_seq
    for event in owner.cache.snapshot(since=last_seq):
        await websocket.send_json(asdict(event))
        sent = event.seq
    while True:
        event = await queue.get()
        if event.seq <= sent:
            continue
        await websocket.send_json(asdict(event))
        sent = event.seq


async def _receive_loop(websocket: WebSocket,
                        owner: "BackendOwner") -> None:
    """上行泵:逐帧解析受理;断开即退出(外层收口清理)。"""
    while True:
        raw = await websocket.receive_text()
        code = await _handle_upstream(raw, owner)
        if code is not None:
            await _send_protocol_error(websocket, code)


async def _handle_upstream(raw: str, owner: "BackendOwner") -> Optional[str]:
    """上行帧受理:返回 protocol_error 错误码(None=已受理)。"""
    try:
        frame = json.loads(raw)
    except json.JSONDecodeError:
        return CODE_BAD_FRAME
    if not isinstance(frame, dict) or "type" not in frame:
        return CODE_BAD_FRAME
    if frame["type"] == "input":
        text = frame.get("text")
        if not isinstance(text, str) or not text.strip():
            return CODE_INPUT_EMPTY
        await owner.submit(text)
        return None
    if frame["type"] == "decision":
        # 契约已定、接线在 07:当前无挂起审批可应答
        return CODE_DECISION_UNAVAILABLE
    return CODE_UNKNOWN_TYPE


async def _send_protocol_error(websocket: WebSocket, code: str) -> None:
    await websocket.send_json({
        "seq": PROTOCOL_ERROR_SEQ,
        "type": "protocol_error",
        "origin": PROTOCOL_ERROR_ORIGIN,
        "payload": {"code": code},
    })
