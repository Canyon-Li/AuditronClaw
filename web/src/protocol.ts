/* WS 契约的 TS 镜像:权威定稿在 entry/web_ws.py 模块 docstring,
 * 本文件只做类型对齐,不引入浏览器特有语义(契约客户端无关,
 * 远期 TUI 客户端同协议)。 */

/** 回合来源(心跳回合客户端过滤,主视图只展示操作员回合)。 */
export type TurnOrigin = "human" | "heartbeat" | "bench" | "unattended";

/** 重启重建事件的来源标记:后端重启后缓存空,历史从会话存档按消息粒度
 * 恢复,与实时流可区分(存档查不到各回合来源,重建不猜来源)。 */
export type HistoryOrigin = "history";

export type ToolCallPayload = { name: string; args: Record<string, unknown> };
export type ToolResultPayload = { tool: string; result: string };
export type ReplyPayload = { content: string; final: boolean };
export type ApprovalRequestPayload = {
  tool: string;
  args: Record<string, unknown>;
  risk_class: string;
  reason: string;
};
export type TurnEndPayload = {
  tool_calls: { tool: string; args: Record<string, unknown> }[];
  tool_results: { tool: string; result: string }[];
  reply: string;
};
export type TurnErrorPayload = { error: string };

export type ProtocolErrorCode =
  | "bad_frame"
  | "unknown_type"
  | "input_empty"
  | "decision_unavailable";

/** protocol_error 帧:服务进程自身产生,seq=0、origin="server",不入回合流。 */
export type ProtocolErrorEnvelope = {
  seq: 0;
  type: "protocol_error";
  origin: "server";
  payload: { code: ProtocolErrorCode };
};

/** 下行信封 {seq, type, origin, payload}:seq 进程内单调,断线重连带 last_seq 补发;
 * 历史重建事件的五种回合事件同型,origin 为 history。 */
export type StreamOrigin = TurnOrigin | HistoryOrigin;
export type Envelope =
  | { seq: number; type: "tool_call"; origin: StreamOrigin; payload: ToolCallPayload }
  | { seq: number; type: "tool_result"; origin: StreamOrigin; payload: ToolResultPayload }
  | { seq: number; type: "reply"; origin: StreamOrigin; payload: ReplyPayload }
  | { seq: number; type: "approval_request"; origin: StreamOrigin; payload: ApprovalRequestPayload }
  | { seq: number; type: "turn_end"; origin: StreamOrigin; payload: TurnEndPayload }
  | { seq: number; type: "turn_error"; origin: StreamOrigin; payload: TurnErrorPayload }
  | ProtocolErrorEnvelope;

/** 上行帧:input 入队成 human 回合;decision 为审批应答(接线随后续票)。 */
export type UpstreamMessage =
  | { type: "input"; text: string }
  | { type: "decision"; choice: "once" | "always" | "deny" };
