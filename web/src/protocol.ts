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
/** write 审批复预览的统一 diff 行(可选字段):t 分四态,text 含前缀字符
 * (ctx 空格 / add + / del - / h 为 @@ 段头);无此字段的审批回落整段预览。 */
export type DiffLine = { t: "ctx" | "add" | "del" | "h"; text: string };

export type ApprovalRequestPayload = {
  tool: string;
  args: Record<string, unknown>;
  risk_class: string;
  reason: string;
  /** 引擎审批超时的真实值(服务端构造期读 AUDITRONCLAW_APPROVAL_TIMEOUT,
   * 默认 300):倒计时以此为限,客户端不自设期限。 */
  timeout_seconds: number;
  /** write_office_file 专用:统一 diff 预览行(无变更/参数不可信时缺省)。 */
  diff?: DiffLine[];
  /** diff 的归一化相对路径(office 根基准,正斜杠)。 */
  filename?: string;
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
  | "decision_invalid"
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

/** 审批应答三选:once=允许一次 / always=永久允许(入规则生效)/ deny=拒绝。 */
export type DecisionChoice = "once" | "always" | "deny";

/** 上行帧:input 入队成 human 回合;decision 回填属主进程内挂起的审批,
 * 同回合续行。挂起在服务进程不在连接上,断线重连/刷新后同一笔仍可应答;
 * 不答即拒由引擎超时兜底。 */
export type UpstreamMessage =
  | { type: "input"; text: string }
  | { type: "decision"; choice: DecisionChoice };
