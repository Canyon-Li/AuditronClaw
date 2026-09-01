/* 组件取自 beautifului.dev(https://www.beautifului.dev/),站页 copy-paste 分发
 * 组件名 Approval Card · 取用日期 2026-09-01 · MIT · Copyright (c) 2026 Shane Levine
 * 本仓改动:按 Web 终端审批动线改造——原作是多题问答卡(翻题/滚轮计数/自定义答案/
 * Skip-Continue 页脚),改为单笔高危调用待批卡:允许一次/永久允许/拒绝 三选项 +
 * 倒计时与风险级/依据/参数行;站方 Button/GlideMenu 基元未随取件分发,以同视觉
 * 语言自绘;卡片骨架、入场动画、回执胶囊沿用原作形态。
 * 07 票接线语义:onDecision 只在操作员点选时触发(经 WS decision 帧回填);
 * 倒计时归零不发包——引擎超时才是权威,本地只收口显示,流上后续事件
 * (拒绝的 tool_result 等)经 settledByTimeout 复核收口。超时与手动拒绝的
 * 回显同为"✗ 已拒绝",来源区别走审计回执的 source 字段。 */

"use client";

import { useEffect, useState, type ReactNode } from "react";
import CodeBlock from "./CodeBlock";
import type { DecisionChoice } from "../protocol";

/** 三种审批决定,与 WS 契约的 decision 帧 choice 对齐(权威在 entry/web_ws)。 */
export type ApprovalChoice = DecisionChoice;

export type ApprovalLabels = {
  allowOnce: string;
  allowAlways: string;
  deny: string;
  approvedOnce: string;
  approvedAlways: string;
  denied: string;
};

const DEFAULT_LABELS: ApprovalLabels = {
  allowOnce: "允许一次",
  allowAlways: "永久允许",
  deny: "拒绝",
  approvedOnce: "✓ 已批准(仅本次)",
  approvedAlways: "✓ 已批准并永久允许",
  denied: "✗ 已拒绝",
};

/** 已决态按决定一次取齐:回显文案键、胶囊/圆点配色(绿=批准,红=拒绝)、勾叉图标。 */
const ECHO_KEY: Record<ApprovalChoice, keyof ApprovalLabels> = {
  once: "approvedOnce",
  always: "approvedAlways",
  deny: "denied",
};

const RESULT_STYLE: Record<ApprovalChoice, { pill: string; dot: string; icon: ReactNode }> = {
  once: { pill: "bg-green-tint text-green", dot: "bg-green", icon: <path d="M20 6L9 17l-5-5" /> },
  always: { pill: "bg-green-tint text-green", dot: "bg-green", icon: <path d="M20 6L9 17l-5-5" /> },
  deny: { pill: "bg-red-tint text-red", dot: "bg-red", icon: <path d="M18 6L6 18M6 6l12 12" /> },
};

const ENTRY_ANIM = "fade-up 380ms cubic-bezier(0.23,1,0.32,1) both";

function formatClock(total: number) {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ApprovalCard({
  toolName,
  script,
  filename,
  riskClass,
  reason,
  metaLines = [],
  timeoutSeconds = 300,
  settledByTimeout = false,
  labels,
  onDecision,
  className,
}: {
  /** 待批工具调用名,如 "bash"。 */
  toolName: string;
  /** 待执行脚本/参数全文,行号化全文入卡。 */
  script: string;
  /** 脚本文件名(CodeBlock 头展示)。 */
  filename?: string;
  /** 风险级(副作用分级),如 write / execute。 */
  riskClass?: string;
  /** 分级依据(人批的是具体动作,不是类别印象)。 */
  reason?: string;
  /** 除脚本外的其余参数行(如 filepath: "reports/daily.md")。 */
  metaLines?: string[];
  /** 超时秒数;到点未答,引擎按拒绝收场。 */
  timeoutSeconds?: number;
  /** 回合已越过此审批而卡上无操作员决定:引擎侧已终局(不答即拒),卡面收口。 */
  settledByTimeout?: boolean;
  labels?: Partial<ApprovalLabels>;
  /** 决定回调:操作员点选时触发一次(倒计时归零不触发,引擎超时是权威)。 */
  onDecision?: (choice: ApprovalChoice) => void;
  className?: string;
}) {
  const t = { ...DEFAULT_LABELS, ...labels };
  const [picked, setPicked] = useState<ApprovalChoice | null>(null);
  const [expired, setExpired] = useState(false); // 本地倒计时归零
  const [left, setLeft] = useState(timeoutSeconds);

  /* 卡面已决态纯推导:操作员点选 > 引擎侧终局(流上收口,不答即拒)>
   * 本地倒计时归零(只收口显示,引擎超时才是权威)。 */
  const choice: ApprovalChoice | null =
    picked ?? (settledByTimeout || expired ? "deny" : null);
  const byTimeout = picked === null && (settledByTimeout || expired);

  useEffect(() => {
    if (choice) return;
    const timer = setTimeout(() => {
      if (left <= 1) {
        setExpired(true);
        return;
      }
      setLeft((current) => current - 1);
    }, 1000);
    return () => clearTimeout(timer);
  }, [choice, left]);

  const decide = (next: ApprovalChoice) => {
    if (choice) return;
    setPicked(next);
    onDecision?.(next);
  };

  const echo = choice ? t[ECHO_KEY[choice]] : null;
  const result = choice ? RESULT_STYLE[choice] : null;

  return (
    <div className={`w-full max-w-105${className ? ` ${className}` : ""}`}>
      <div
        className="overflow-hidden rounded-card bg-surface shadow-card"
        style={{ animation: ENTRY_ANIM }}
      >
        <div className="primitive-card-pad">
          {/* 标题行:待批工具 + 倒计时 */}
          <div className="flex items-center gap-2">
            <span className="text-[14px] font-medium text-ink">高危调用待审批</span>
            <span className="inline-flex h-5.5 items-center rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-ink-2 shadow-hairline">
              {toolName}
            </span>
            {riskClass && (
              <span className="inline-flex h-5.5 items-center rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-red shadow-hairline">
                {riskClass}
              </span>
            )}
            <span
              aria-label="审批倒计时"
              className={`ml-auto font-mono text-[12px] tabular-nums ${
                !choice && left <= 30 ? "text-red" : "text-ink-3"
              }`}
            >
              {formatClock(byTimeout ? 0 : left)}
            </span>
          </div>
          {/* 分级依据 + 其余参数行 */}
          {reason && (
            <p className="mt-1.5 text-[12px] leading-relaxed text-ink-3">
              依据:<span className="text-ink-2">{reason}</span>
            </p>
          )}
          {metaLines.length > 0 && (
            <p className="mt-1 font-mono text-[11.5px] leading-relaxed text-ink-3">
              {metaLines.map((line) => (
                <span key={line} className="block break-all">
                  {line}
                </span>
              ))}
            </p>
          )}
          {/* 待执行脚本全文 */}
          <div className="mt-2.5">
            <CodeBlock
              variant="Code"
              lines={script.split("\n")}
              filename={filename ?? `${toolName}.txt`}
              labels={{ copy: "复制", copied: "已复制" }}
            />
          </div>
        </div>

        {/* footer — 三选项;已决出则回显胶囊 */}
        <div className="primitive-card-footer flex items-center justify-end gap-1.5 border-t border-line">
          {!result ? (
            <>
              <button
                type="button"
                onClick={() => decide("deny")}
                className="flex h-7 items-center rounded-control px-2.5 text-[12.5px] font-medium text-ink-3 transition-colors duration-100 hover:bg-hover hover:text-ink"
              >
                {t.deny}
              </button>
              <button
                type="button"
                onClick={() => decide("once")}
                className="flex h-7 items-center rounded-control bg-surface px-2.5 text-[12.5px] font-medium text-ink shadow-btn transition-colors duration-100 hover:bg-hover"
              >
                {t.allowOnce}
              </button>
              <button
                type="button"
                onClick={() => decide("always")}
                className="flex h-7 items-center rounded-control bg-green-tint px-2.5 text-[12.5px] font-medium text-green transition-opacity duration-100 hover:opacity-90"
              >
                {t.allowAlways}
              </button>
            </>
          ) : (
            <span
              className={`inline-flex items-center gap-1.5 rounded-full py-1 pr-2.5 pl-1 text-[12.5px] font-medium ${result.pill}`}
              style={{ animation: "pop-in 260ms cubic-bezier(0.23,1,0.32,1) both" }}
            >
              <span
                className={`flex size-4.5 items-center justify-center rounded-full text-white ${result.dot}`}
              >
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="3"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  {result.icon}
                </svg>
              </span>
              {echo}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
