/* 终端页(05 票):WS 真流上屏——PromptBar 提交经 input 帧入队,回合事件
 * 实时驱动 ToolChips / StreamingText;心跳回合客户端过滤(origin 字段,
 * 主视图只展示操作员回合);断线重连与刷新经 last_seq 补发不丢画面。
 * 07 票:审批动线接线——回合内 approval_request 事件落成审批卡(工具/
 * 风险级/依据/完整参数 + 引擎超时倒计时,payload 带真实秒数),三选一经 decision 帧回填、同回合
 * 续行;卡旁点开见该笔审计回执(审计旁路取数)。挂起的判定纯推导:审批
 * 请求是回合末帧即待答,其后有事件即引擎侧已终局(不答即拒)。历史段
 * (origin=history)不含审批过程事件,防御性以事件行呈现。
 * 06 票:重启重建段(origin=history)以分隔标注呈现在实时流之上,
 * 历史回合已收尾、不带运行态。
 * 10 票:视觉壳对齐操作员 v2 原型(2026-09-02)——毛玻璃 sticky 页头 + 状态
 * pill 化(连接点色 / 审批等待 pulse / 后台帧计数,全前端可推导),阅读列
 * 420px → 760px,输入条停靠式(sticky 底部 + 向下渐变),滚动跟随仅当操作员
 * 已在底部(不打断回看);回合保留输入回显(WS 流不带操作员输入帧,回显
 * 只覆盖本连接生命周期内提交的回合)。
 * 11 票(2026-09-02 第二轮,规格来自操作员最新版设计):问答分离——输入
 * 回显改右侧气泡(❯ 前缀),回复与工具轨迹留左侧,轮距 32px;审批时刻
 * 让位——待答时历史段与其余回合退暗(opacity .45 + 去饱和),审批所在
 * 回合豁免,决定后 350ms 恢复;审批信封 seq 传入卡片供落章署号。 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ToolDetailLine, ToolStep } from "./components/ToolChips";
import ToolChips from "./components/ToolChips";
import PromptBar from "./components/PromptBar";
import StreamingText, { type StreamingToken } from "./components/StreamingText";
import ApprovalCard, { type ApprovalChoice } from "./components/ApprovalCard";
import ApprovalReceipt from "./components/ApprovalReceipt";
import { ShieldIcon } from "./components/icons";
import type { DecisionChoice, Envelope } from "./protocol";
import { useTerminalStream } from "./useTerminalStream";

// ============ 回合视图模型:信封流 → 操作员回合段 ============

/** 主视图的一个回合:单一 origin 的连续事件段(单 worker 串行,回合间不交错)。 */
type TurnView = {
  key: number; // 段首事件 seq(渲染键)
  events: Envelope[];
  complete: boolean; // turn_end / turn_error 已落定
};

type TurnModel = {
  historyTurns: TurnView[]; // 重启重建段(origin=history,后端重启前已收尾的回合)
  turns: TurnView[]; // 仅操作员回合(心跳等后台回合过滤,计数另报)
  lastHumanSeq: number; // 最近一帧操作员事件的 seq(提交水位线的对照)
  backgroundEvents: number; // 被过滤的后台帧数(呈现层可交代心跳在跑)
};

function groupTurns(envelopes: Envelope[]): TurnModel {
  const all: TurnView[] = [];
  for (const event of envelopes) {
    const settled = event.type === "turn_end" || event.type === "turn_error";
    const last = all[all.length - 1];
    if (last && !last.complete) {
      last.events.push(event);
      last.complete = settled;
    } else {
      all.push({ key: event.seq, events: [event], complete: settled });
    }
  }
  const historyTurns: TurnView[] = [];
  const turns: TurnView[] = [];
  let backgroundEvents = 0;
  for (const turn of all) {
    const origin = turn.events[0].origin;
    if (origin === "history") historyTurns.push(turn);
    else if (origin === "human") turns.push(turn);
    else backgroundEvents += turn.events.length;
  }
  const lastTurn = turns[turns.length - 1];
  return {
    historyTurns,
    turns,
    lastHumanSeq: lastTurn ? lastTurn.events[lastTurn.events.length - 1].seq : 0,
    backgroundEvents,
  };
}

/** 回合是否挂着待答审批:末帧是 approval_request(引擎应答后续行,末帧
 * 之外即已终局)。心跳回合构造上不产生审批卡(引擎保证,此为呈现层复验:
 * 心跳段在 origin 过滤时已整体出主视图)。 */
function pendingApprovalSeq(turn: TurnView): number | null {
  if (turn.complete) return null;
  const last = turn.events[turn.events.length - 1];
  return last.type === "approval_request" ? last.seq : null;
}

// ============ 事件 → 组件数据:工具步 / 审批卡 / 回复帧 ============

/* 图标按工具名猜读/写/执行,猜不中落执行态(展示层提示,分级以审批门为准) */
const ICON_HINTS: [RegExp, string][] = [
  [/read|list|search|get|query|glob|grep/i, "read"],
  [/write|edit|create|update|delete|save|modify|patch/i, "write"],
];

function iconForTool(name: string): string {
  for (const [pattern, icon] of ICON_HINTS) {
    if (pattern.test(name)) return icon;
  }
  return "run";
}

/** 工具结果入详情行:长文本截断,换行拆行(原文可查审计,不在此全文铺开)。 */
function resultLines(result: string, cap = 500): ToolDetailLine[] {
  const text = result.length > cap ? `${result.slice(0, cap)}…(已截断)` : result;
  return text.split("\n").map((line) => ({ text: line }));
}

/** approval_request 事件是否落成工具步:历史段防御性呈现(存档不含审批
 * 过程事件,不该出现;出现了也不给交互)。 */
function stepsForTurn(events: Envelope[], approvalAsStep: boolean): ToolStep[] {
  const steps: ToolStep[] = [];
  for (const event of events) {
    if (event.type === "tool_call") {
      steps.push({
        icon: iconForTool(event.payload.name),
        label: `调用 ${event.payload.name}`,
        chip: event.payload.name,
        mono: true,
        detailMono: true,
        detail: [{ text: JSON.stringify(event.payload.args) }],
      });
    } else if (approvalAsStep && event.type === "approval_request") {
      steps.push({
        icon: "think",
        label: "审批请求(历史段,交互不还原)",
        chip: event.payload.tool,
        mono: true,
        detailMono: false,
        detail: [
          { text: `风险级 ${event.payload.risk_class} · ${event.payload.reason}` },
          { text: JSON.stringify(event.payload.args) },
        ],
      });
    } else if (event.type === "tool_result") {
      // 结果挂到最近一个同工具调用步;找不到单独成行(防御:结果先于调用)
      let target = -1;
      for (let i = steps.length - 1; i >= 0; i--) {
        if (steps[i].chip === event.payload.tool) {
          target = i;
          break;
        }
      }
      const lines = resultLines(event.payload.result);
      if (target >= 0) steps[target].detail.push(...lines);
      else
        steps.push({
          icon: "run",
          label: `结果 ${event.payload.tool}`,
          chip: event.payload.tool,
          mono: true,
          detailMono: true,
          detail: lines,
        });
    }
  }
  return steps;
}

/* 审批卡的码块正文:长文本参数(命令/脚本/内容)原文入块,其余参数成行;
 * 没有可认的长文本参数时全量 JSON 入块(完整参数永远可见)。 */
const SCRIPT_ARG_KEYS = ["command", "script", "code", "content", "cmd", "shell"];

function approvalBody(args: Record<string, unknown>): {
  script: string;
  meta: string[];
} {
  const main = SCRIPT_ARG_KEYS.find(
    (key) => typeof args[key] === "string" && args[key] !== "",
  );
  if (main) {
    const rest = Object.entries(args).filter(([key]) => key !== main);
    return {
      script: args[main] as string,
      meta: rest.map(([key, value]) => `${key}: ${JSON.stringify(value)}`),
    };
  }
  return { script: JSON.stringify(args, null, 2), meta: [] };
}

type ReplyEvent = Extract<Envelope, { type: "reply" }>;
type TurnErrorEvent = Extract<Envelope, { type: "turn_error" }>;
type ApprovalEvent = Extract<Envelope, { type: "approval_request" }>;

/** 逐字 55ms 会把长回复拖成分钟级:空白处切词,中日韩连续段按 2 字一帧。 */
function tokenizeReply(content: string): StreamingToken[] {
  return content
    .split(/(?<=\s)|(?<=[一-鿿]{2})/)
    .filter((token) => token !== "")
    .map((text) => ({ text }));
}

// ============ 原型形态的小件:输入回显 / 等待三点 ============

/** 回合首行的操作员输入回显(原型 .echo 气泡):右对齐,❯ 终端前缀;
 * 底色为面层色混入 4% 墨色,右下小角当尾巴(问答分离:问右 / 答左)。 */
function EchoLine({ text }: { text: string }) {
  return (
    <p
      className="ml-auto max-w-[85%] rounded-[10px_10px_2px_10px] px-3 py-2 text-right font-mono text-[13px] leading-[1.6] text-ink-2 [overflow-wrap:anywhere] shadow-[0_0_0_1px_var(--line-strong)] max-[600px]:max-w-[90%]"
      style={{
        background: "color-mix(in oklab, var(--field), var(--ink) 4%)",
      }}
    >
      <span className="mr-1.5 select-none text-ink-3">❯</span>
      {text}
    </p>
  );
}

/** 入队/引擎运行中的等待提示(原型 .pending 三点波形)。 */
function PendingDots({ label }: { label: string }) {
  return (
    <div
      role="status"
      className="flex items-center gap-2.5 text-[12.5px] text-ink-3"
    >
      <span className="inline-flex gap-1" aria-hidden="true">
        {[0, 150, 300].map((delay) => (
          <span
            key={delay}
            className="size-[5px] rounded-full bg-ink-3"
            style={{ animation: "wave 1.2s infinite", animationDelay: `${delay}ms` }}
          />
        ))}
      </span>
      {label}
    </div>
  );
}

// ============ 页面 ============

const STATUS_TEXT: Record<string, string> = {
  connecting: "连接中…",
  open: "已连接",
  reconnecting: "断线重连中…",
};

function TurnSection({
  turn,
  running,
  echo,
  token,
  dimmed,
  onDecision,
  remountOf,
}: {
  turn: TurnView;
  running: boolean;
  /** 本连接内提交、归属于该回合的操作员输入(刷新后 WS 流不带输入帧,无可回显)。 */
  echo: string | null;
  token: string;
  /** 审批待答的让位态:其余回合退暗,审批所在回合豁免(原型 .turn 原样)。 */
  dimmed: boolean;
  onDecision: (seq: number, choice: ApprovalChoice, stillPending: boolean) => void;
  /** 应答未送达需重选的审批 seq(该卡重挂载复位,已选态撤回)。 */
  remountOf: number | null;
}) {
  const steps = useMemo(
    () => stepsForTurn(turn.events, turn.events[0].origin === "history"),
    [turn],
  );
  const approvals = turn.events.filter(
    (event): event is ApprovalEvent => event.type === "approval_request",
  );
  const pendingSeq = pendingApprovalSeq(turn);
  const calls = turn.events.filter((event) => event.type === "tool_call").length;
  const replies = turn.events.filter(
    (event): event is ReplyEvent => event.type === "reply" && event.payload.final,
  );
  const error = turn.events.find(
    (event): event is TurnErrorEvent => event.type === "turn_error",
  );
  const awaitingApproval = pendingSeq !== null;

  return (
    <section
      className={`flex flex-col gap-2.5 transition-[opacity,filter] duration-[350ms] ease ${
        dimmed ? "opacity-45 saturate-75" : ""
      }`}
    >
      {echo && <EchoLine text={echo} />}
      {running && !awaitingApproval && steps.length === 0 && replies.length === 0 && (
        <PendingDots label="会话引擎运行中" />
      )}
      {steps.length > 0 && (
        <ToolChips
          steps={steps}
          diffs={[]}
          diffLines={{}}
          labels={{
            header: calls > 0 ? `本轮 ${calls} 次工具调用` : "本轮事件",
            more: "",
          }}
        />
      )}
      {approvals.map((event) => {
        // 审批请求之后还有事件 = 引擎侧已终局(不答即拒);是末帧即待答
        const settled = event.seq !== pendingSeq;
        const body = approvalBody(event.payload.args);
        return (
          <div key={event.seq} className="flex flex-col gap-1.5">
            <ApprovalCard
              key={event.seq === remountOf ? `${event.seq}:retry` : event.seq}
              toolName={event.payload.tool}
              script={body.script}
              metaLines={body.meta}
              riskClass={event.payload.risk_class}
              reason={event.payload.reason}
              timeoutSeconds={event.payload.timeout_seconds}
              settledByTimeout={settled}
              requestSeq={event.seq}
              onDecision={(choice) => onDecision(event.seq, choice, !settled)}
            />
            {settled && (
              <ApprovalReceipt
                token={token}
                tool={event.payload.tool}
                args={event.payload.args}
              />
            )}
          </div>
        );
      })}
      {replies.map((event) => (
        <StreamingText
          key={event.seq}
          content={tokenizeReply(event.payload.content)}
          sources={[]}
          followUps={[]}
          labels={{ sources: "0 个来源" }}
          loop={false}
          fill
        />
      ))}
      {error && (
        <p className="font-mono text-[11px] text-red">回合异常:{error.payload.error}</p>
      )}
    </section>
  );
}

export default function TerminalPage({ token }: { token: string }) {
  const { status, envelopes, protocolError, sendInput, sendDecision } =
    useTerminalStream(token);
  const model = useMemo(() => groupTurns(envelopes), [envelopes]);
  const lastTurn = model.turns[model.turns.length - 1];
  const pendingSeq = lastTurn ? pendingApprovalSeq(lastTurn) : null;
  /* 审批时刻的让位锚点:待答审批所在回合(全页只可能一处——单 worker
   * 串行,审批挂起时回合不收尾);其余回合与历史段据此退暗 */
  const approvalTurn = pendingSeq !== null ? (lastTurn ?? null) : null;

  /* 审批应答未送达时撤回该卡已选态(key 换名重挂载),提示重连后重选;
   * 引擎超时兜底,不会无限挂起 */
  const [remountOf, setRemountOf] = useState<number | null>(null);
  const [decisionLost, setDecisionLost] = useState<number | null>(null);

  /* 提交水位线:提交后到本回合首个事件前,队列/引擎侧在信封流上无痕迹,
   * 以已见操作员事件 seq 为水位,新事件出现即视为提交已开跑——纯推导,
   * 不靠 effect 清标记。sentLog 留住历次提交(水位线 + 文本):末条喂
   * "已入队"提示(submitted),已开跑的继续作为各回合的输入回显 */
  const [sentLog, setSentLog] = useState<{ atSeq: number; text: string }[]>([]);
  const submitted = sentLog.length > 0 ? sentLog[sentLog.length - 1] : null;
  const [undelivered, setUndelivered] = useState<string | null>(null);
  const pending = submitted !== null && submitted.atSeq >= model.lastHumanSeq;
  const running = pending || (lastTurn !== undefined && !lastTurn.complete);

  /* 回合回显配对:串行队列先提交先开跑,按序消费水位线之下的提交日志;
   * 后端重启回卷(seq 回落)后,旧序数空间的悬挂条目永久配不上对,按当前
   * 水位线剔除,不卡住后续配对 */
  const echoByKey = useMemo(() => {
    const byKey = new Map<number, string>();
    const live = sentLog.filter((entry) => entry.atSeq <= model.lastHumanSeq);
    let i = 0;
    for (const turn of model.turns) {
      const firstSeq = turn.events[0].seq;
      if (i < live.length && live[i].atSeq < firstSeq) {
        byKey.set(turn.key, live[i].text);
        i++;
      }
    }
    return byKey;
  }, [model.turns, sentLog, model.lastHumanSeq]);

  /* 滚动跟随(原型 nearBottom 语义):仅在操作员已在底部时跟随新内容,
   * 回看历史时不打断;滚动位置由滚动事件记入 ref,新内容落地后按记档决定 */
  const atBottomRef = useRef(true);
  useEffect(() => {
    const onScroll = () => {
      atBottomRef.current =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 140;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useLayoutEffect(() => {
    if (atBottomRef.current) {
      window.scrollTo(0, document.documentElement.scrollHeight);
    }
  }, [envelopes.length, submitted]);

  /* 流式回复按词上屏、详情行展开都在信封不变的情况下长高:回合运行中
   * 以固定节拍续跟(仍在底部时),补齐信封驱动之外的跟随 */
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => {
      if (atBottomRef.current) {
        window.scrollTo(0, document.documentElement.scrollHeight);
      }
    }, 120);
    return () => window.clearInterval(timer);
  }, [running]);

  const handleSend = (text: string) => {
    if (sendInput(text)) {
      setSentLog((log) => [...log, { atSeq: model.lastHumanSeq, text }]);
      setUndelivered(null);
    } else {
      // 未入队不打"已入队":提示如实,消息不排队重发
      setUndelivered(text);
    }
  };

  const handleDecision = (
    seq: number,
    choice: DecisionChoice,
    stillPending: boolean,
  ) => {
    if (!stillPending) return; // 引擎侧已终局:不发包(迟到的点选不作数)
    if (sendDecision(choice)) {
      setDecisionLost(null);
      setRemountOf(null);
    } else {
      // 连接未就绪,应答没有送达:撤回已选态,重连后重选(不谎报已批准)
      setDecisionLost(seq);
      setRemountOf(seq);
    }
  };

  return (
    <div className="flex min-h-screen flex-col">
      <header
        className="sticky top-0 z-40 border-b border-line-soft backdrop-blur-md"
        style={{
          background: "color-mix(in oklab, var(--page) 84%, transparent)",
        }}
      >
        <div className="mx-auto flex w-full max-w-190 flex-wrap items-center gap-2.5 px-5 py-2.5 max-[600px]:px-3.5">
          <div className="flex items-center gap-2 text-sm font-semibold text-ink">
            <span className="grid place-items-center text-accent">
              <ShieldIcon size={16} />
            </span>
            <span>AuditronClaw</span>
            <span className="h-3 w-px bg-line-strong" />
            <span className="font-normal text-ink-2">Web 终端</span>
          </div>
          <div className="ml-auto flex flex-wrap gap-1.5">
            <span className="inline-flex h-5.5 items-center gap-1.5 whitespace-nowrap rounded-chip bg-field px-2.25 font-mono text-[11.5px] text-ink-2 shadow-hairline">
              <span
                className={`size-1.5 rounded-full ${status === "open" ? "bg-green" : "bg-ink-3"}`}
              />
              {STATUS_TEXT[status]}
            </span>
            {pendingSeq !== null && (
              <span className="inline-flex h-5.5 items-center gap-1.5 whitespace-nowrap rounded-chip bg-orange-tint px-2.25 font-mono text-[11.5px] text-orange">
                <span
                  className="size-1.5 rounded-full bg-orange"
                  style={{ animation: "pulse 1.6s infinite" }}
                />
                审批等待应答
              </span>
            )}
            {model.backgroundEvents > 0 && (
              <span
                title={`已过滤 ${model.backgroundEvents} 帧后台回合(心跳)`}
                className="inline-flex h-5.5 items-center gap-1.5 whitespace-nowrap rounded-chip bg-field px-2.25 font-mono text-[11.5px] text-ink-2 shadow-hairline"
              >
                后台 {model.backgroundEvents} 帧
              </span>
            )}
          </div>
        </div>
        {(protocolError || decisionLost !== null) && (
          <div className="mx-auto w-full max-w-190 space-y-0.5 px-5 pb-1.5 font-mono text-[11px] text-red max-[600px]:px-3.5">
            {protocolError && <p>上行帧被拒:{protocolError}</p>}
            {decisionLost !== null && (
              <p>审批应答未送达(连接未就绪):重连后请在审批卡上重选</p>
            )}
          </div>
        )}
      </header>

      <main className="mx-auto flex w-full max-w-190 flex-1 flex-col gap-6 px-5 pt-6.5 pb-[150px] max-[600px]:px-3.5 max-[600px]:pt-5 max-[600px]:pb-[130px]">
        {model.turns.length === 0 &&
          model.historyTurns.length === 0 &&
          !pending && (
            <p className="text-[12.5px] text-ink-3">
              尚无回合:输入消息开始第一个回合。
            </p>
          )}

        {model.historyTurns.length > 0 && (
          <section
            className={`transition-[opacity,filter] duration-[350ms] ease ${
              approvalTurn !== null
                ? "opacity-45 saturate-75"
                : "opacity-[0.78]"
            }`}
          >
            <p className="flex items-center gap-3 font-mono text-[11px] tracking-[0.06em] text-ink-3">
              <span className="h-px flex-1 bg-line" />
              服务重启前的历史 · 自动从会话存档恢复,审批过程不还原
              <span className="h-px flex-1 bg-line" />
            </p>
            <div className="mt-6 flex flex-col gap-8">
              {model.historyTurns.map((turn) => (
                <TurnSection
                  key={turn.key}
                  turn={turn}
                  running={false}
                  echo={null}
                  token={token}
                  dimmed={false}
                  onDecision={handleDecision}
                  remountOf={remountOf}
                />
              ))}
            </div>
          </section>
        )}

        <div className="flex flex-col gap-8">
          {model.turns.map((turn) => (
            <TurnSection
              key={turn.key}
              turn={turn}
              running={running && turn === lastTurn}
              echo={echoByKey.get(turn.key) ?? null}
              token={token}
              dimmed={approvalTurn !== null && turn !== approvalTurn}
              onDecision={handleDecision}
              remountOf={remountOf}
            />
          ))}
          {pending && submitted && (
            <section
              className={`flex flex-col gap-2.5 transition-[opacity,filter] duration-[350ms] ease ${
                approvalTurn !== null ? "opacity-45 saturate-75" : ""
              }`}
            >
              <EchoLine text={submitted.text} />
              <PendingDots label="已入队,等待引擎回合" />
            </section>
          )}
        </div>
      </main>

      <div
        className="sticky bottom-0 z-50 px-5 pt-3.5 pb-[calc(16px+env(safe-area-inset-bottom))] max-[600px]:px-3.5 max-[600px]:pt-2.5 max-[600px]:pb-[calc(12px+env(safe-area-inset-bottom))]"
        style={{
          background: "linear-gradient(to top, var(--page) 62%, transparent)",
        }}
      >
        <div className="mx-auto w-full max-w-190">
          {undelivered && status !== "open" && (
            <p className="mb-1.5 font-mono text-[11px] text-red">
              未发送(连接未就绪,重连后请重发):{undelivered}
            </p>
          )}
          <PromptBar placeholder="输入消息,回车发送…" onSend={handleSend} />
        </div>
      </div>
    </div>
  );
}
