/* 终端页(05 票):WS 真流上屏——PromptBar 提交经 input 帧入队,回合事件
 * 实时驱动 ToolChips / StreamingText / LoadingState / Thinking;心跳回合
 * 客户端过滤(origin 字段,主视图只展示操作员回合);断线重连与刷新经
 * last_seq 补发不丢画面。审批卡交互动线(ApprovalCard ↔ decision 帧)
 * 随审批票接入,本页对 approval_request 呈现为事件行。
 * 06 票:重启重建段(origin=history)以分隔标注呈现在实时流之上,
 * 历史回合已收尾、不带运行态。 */

import { useMemo, useState } from "react";
import type { ToolDetailLine, ToolStep } from "./components/ToolChips";
import ToolChips from "./components/ToolChips";
import LoadingState from "./components/LoadingState";
import PromptBar from "./components/PromptBar";
import StreamingText, { type StreamingToken } from "./components/StreamingText";
import Thinking from "./components/Thinking";
import type { Envelope } from "./protocol";
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

// ============ 事件 → 组件数据:工具步 / 回复帧 ============

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

function stepsForTurn(events: Envelope[]): ToolStep[] {
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
    } else if (event.type === "approval_request") {
      steps.push({
        icon: "think",
        label: "审批请求(交互动线随审批票接入)",
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

type ReplyEvent = Extract<Envelope, { type: "reply" }>;
type TurnErrorEvent = Extract<Envelope, { type: "turn_error" }>;

/** 逐字 55ms 会把长回复拖成分钟级:空白处切词,中日韩连续段按 2 字一帧。 */
function tokenizeReply(content: string): StreamingToken[] {
  return content
    .split(/(?<=\s)|(?<=[一-鿿]{2})/)
    .filter((token) => token !== "")
    .map((text) => ({ text }));
}

// ============ 页面 ============

const STATUS_TEXT: Record<string, string> = {
  connecting: "连接中…",
  open: "已连接",
  reconnecting: "断线重连中…(已收事件重连后自动补发)",
};

function TurnSection({ turn, running }: { turn: TurnView; running: boolean }) {
  const steps = useMemo(() => stepsForTurn(turn.events), [turn]);
  const calls = turn.events.filter(
    (event) => event.type === "tool_call" || event.type === "approval_request",
  ).length;
  const replies = turn.events.filter(
    (event): event is ReplyEvent => event.type === "reply" && event.payload.final,
  );
  const error = turn.events.find(
    (event): event is TurnErrorEvent => event.type === "turn_error",
  );

  return (
    <section className="flex flex-col gap-2">
      {running && steps.length === 0 && replies.length === 0 && (
        <LoadingState label="会话引擎运行中" variant="Drive" />
      )}
      {running && <Thinking variant="Coding" />}
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
  const { status, envelopes, protocolError, sendInput } = useTerminalStream(token);
  const model = useMemo(() => groupTurns(envelopes), [envelopes]);

  /* 提交水位线:提交后到本回合首个事件前,队列/引擎侧在信封流上无痕迹,
   * 以已见操作员事件 seq 为水位,新事件出现即视为提交已开跑——纯推导,
   * 不靠 effect 清标记 */
  const [submitted, setSubmitted] = useState<{
    atSeq: number;
    text: string;
  } | null>(null);
  const [undelivered, setUndelivered] = useState<string | null>(null);
  const pending = submitted !== null && submitted.atSeq >= model.lastHumanSeq;
  const lastTurn = model.turns[model.turns.length - 1];
  const running = pending || (lastTurn !== undefined && !lastTurn.complete);

  const handleSend = (text: string) => {
    if (sendInput(text)) {
      setSubmitted({ atSeq: model.lastHumanSeq, text });
      setUndelivered(null);
    } else {
      // 未入队不打"已入队":提示如实,消息不排队重发
      setUndelivered(text);
    }
  };

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-105 flex-col gap-4 px-4 py-10">
      <header>
        <h1 className="text-[15px] font-medium text-ink">AuditronClaw Web 终端</h1>
        <p className="mt-0.5 text-[12px] text-ink-3">
          回合事件实时上屏 · {STATUS_TEXT[status]}
          {model.backgroundEvents > 0 &&
            ` · 已过滤 ${model.backgroundEvents} 帧后台回合`}
        </p>
        {protocolError && (
          <p className="mt-0.5 font-mono text-[11px] text-red">
            上行帧被拒:{protocolError}
          </p>
        )}
      </header>

      {model.turns.length === 0 && model.historyTurns.length === 0 && !pending && (
        <p className="text-[12px] text-ink-3">尚无回合:输入消息开始第一个回合。</p>
      )}

      {model.historyTurns.length > 0 && (
        <section className="flex flex-col gap-4">
          <p className="text-[11px] text-ink-3">
            —— 服务重启前的历史(自动从会话存档恢复,审批过程不还原)——
          </p>
          <div className="flex flex-col gap-6 opacity-80">
            {model.historyTurns.map((turn) => (
              <TurnSection key={turn.key} turn={turn} running={false} />
            ))}
          </div>
        </section>
      )}

      <div className="flex flex-col gap-6">
        {model.turns.map((turn) => (
          <TurnSection
            key={turn.key}
            turn={turn}
            running={running && turn === lastTurn}
          />
        ))}
        {pending && submitted && (
          <section className="flex flex-col gap-2">
            <p className="font-mono text-[12.5px] text-ink-2">
              <span className="text-ink-3">❯ </span>
              {submitted.text}
            </p>
            <LoadingState label="已入队,等待引擎回合" variant="Drive" />
          </section>
        )}
      </div>

      {undelivered && status !== "open" && (
        <p className="font-mono text-[11px] text-red">
          未发送(连接未就绪,重连后请重发):{undelivered}
        </p>
      )}

      <div className="mt-auto pt-6">
        <PromptBar demo={false} placeholder="输入消息,回车发送…" onSend={handleSend} />
      </div>
    </div>
  );
}
