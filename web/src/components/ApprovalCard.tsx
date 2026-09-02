/* 组件取自 beautifului.dev(https://www.beautifului.dev/),站页 copy-paste 分发
 * 组件名 Approval Card · 取用日期 2026-09-01 · MIT · Copyright (c) 2026 Shane Levine
 * 本仓改动:按 Web 终端审批动线改造——原作是多题问答卡(翻题/滚轮计数/自定义答案/
 * Skip-Continue 页脚),改为单笔高危调用待批卡:允许一次/永久允许/拒绝 三选项 +
 * 倒计时与风险级/依据/参数行;站方 Button/GlideMenu 基元未随取件分发,以同视觉
 * 语言自绘;卡片骨架、入场动画、回执胶囊沿用原作形态。
 * 10 票(2026-09-02):对齐操作员 v2 原型——卡片满列宽(760px 阅读列),
 * 内距 14px/页脚 10×14px,已决态三按钮收起为结果胶囊(绿 ✓/红 ✗,pop-in),
 * 触屏断点三按钮平分 44px 命中区。
 * 11 票(2026-09-02 第二轮,操作员最新版设计):审批时刻形态——待答卡挂
 * 风险描边(write→橙/execute→红,调淡 55%)加两层大模糊投影抬升;标题左侧
 * 38px 环形倒计时(弧长随余量耗尽,≤60s 橙、≤30s 红,替换原右侧纯文字);
 * 决定落章——卡右上圆形印章(86px/触屏 64px,双环斜盖带过冲),批准=绿
 * 「已批准」、拒绝=红「已拒绝」、超时=红「超时拒绝」,章内署审批信封 seq;
 * 结果胶囊保留(once/always 的区分在胶囊,章上不重复),文案去 ✓/✗ 前缀。
 * 12 票(2026-09-02):write 类文件写入的预览升级统一 diff(审批门的本职
 * ——操作员批的就是改动本身):payload 带 diff 行时按 CodeBlock Diff
 * 视图呈现(行号前端推导,头部统计+复制原始 patch);无 diff 场景保持
 * 整段内容预览。
 * 07 票接线语义:onDecision 只在操作员点选时触发(经 WS decision 帧回填);
 * 倒计时归零不发包——引擎超时才是权威,本地只收口显示,流上后续事件
 * (拒绝的 tool_result 等)经 settledByTimeout 复核收口。超时与手动拒绝的
 * 回显同为"✗ 已拒绝",来源区别走审计回执的 source 字段。 */

"use client";

import { useEffect, useState, type ReactNode } from "react";
import CodeBlock, { type DiffRow } from "./CodeBlock";
import type { DecisionChoice, DiffLine } from "../protocol";

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
  approvedOnce: "已批准(仅本次)",
  approvedAlways: "已批准并永久允许",
  denied: "已拒绝",
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

const ENTRY_ANIM = "fade-up 300ms cubic-bezier(0.23,1,0.32,1) both";

/* 待答卡的风险描边(11 票):外圈 1.5px 调淡风险色 + 两层大模糊投影抬升;
 * --risk 由卡片级按 execute→红 / 其余(write)→橙 写入,色值只在现有令牌里取。
 * 单行成串:Tailwind 扫源码文本取类名,断行拼接会让候选不成立、类静默落空 */
const PENDING_RING =
  "[box-shadow:0_0_0_1.5px_color-mix(in_oklab,var(--risk)_55%,transparent),0_12px_32px_oklch(0.24_0.01_258/0.12),0_24px_64px_oklch(0.24_0.01_258/0.1)]";

/** 倒计时弧与数字的告警档位:≤30s 转 hot(红),≤60s 转 warn(橙)。 */
function ringPhase(left: number, frozen: boolean): "warn" | "hot" | null {
  if (frozen) return null; // 已决出:环冻结,不再变色
  if (left <= 30) return "hot";
  if (left <= 60) return "warn";
  return null;
}

/* 统一 diff 行数组 → CodeBlock 的 Diff 行(12 票):行号前端推导——
 * 删行显旧号,ctx/add 显新号,ctx 双侧计数;段头行不带行号 */
function toDiffRows(lines: DiffLine[]): DiffRow[] {
  let oldNum = 0;
  let curNum = 0;
  const rows: DiffRow[] = [];
  for (const line of lines) {
    const pieces = [{ text: line.text }];
    if (line.t === "h") {
      rows.push({ old: null, cur: null, type: "hunk", pieces });
      continue;
    }
    if (line.t === "del") {
      oldNum += 1;
      rows.push({ old: oldNum, cur: null, type: "del", pieces });
      continue;
    }
    curNum += 1;
    if (line.t === "ctx") oldNum += 1;
    rows.push({ old: null, cur: curNum, type: line.t === "add" ? "add" : "ctx", pieces });
  }
  return rows;
}

function formatClock(total: number) {
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

export default function ApprovalCard({
  toolName,
  script,
  filename,
  diff,
  riskClass,
  reason,
  metaLines = [],
  timeoutSeconds = 300,
  settledByTimeout = false,
  requestSeq,
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
  /** write 类文件写入的统一 diff 行(有则按 Diff 视图预览;无则整段内容)。 */
  diff?: DiffLine[];
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
  /** 审批请求的信封 seq(落章署号用;纯展示,不参与判定)。 */
  requestSeq?: number;
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

  /* 倒计时环(11 票):弧长随余量耗尽(dashoffset = 已耗占比 ×100),
   * 终局即冻结在落章瞬间;告警档位只在待答时生效 */
  const ringLeft = byTimeout ? 0 : left;
  const phase = ringPhase(ringLeft, choice !== null);
  const ringColor =
    phase === "hot" ? "var(--red)" : phase === "warn" ? "var(--orange)" : "var(--ink-3)";
  const ringTextClass =
    phase === "hot" ? "text-red" : phase === "warn" ? "text-orange" : "text-ink-2";
  const stampOk = choice === "once" || choice === "always";
  const stampText = stampOk ? "已批准" : byTimeout ? "超时拒绝" : "已拒绝";

  return (
    <div className={`w-full${className ? ` ${className}` : ""}`}>
      <div
        className={`relative overflow-hidden rounded-card bg-surface transition-[box-shadow] duration-300 ${
          choice ? "shadow-card" : PENDING_RING
        }`}
        style={{
          animation: ENTRY_ANIM,
          // 待答挂风险描边色:execute→红,其余(write)→橙;终局撤描边回基座阴影
          ...(!choice && riskClass
            ? ({
                "--risk": riskClass === "execute" ? "var(--red)" : "var(--orange)",
              } as React.CSSProperties)
            : {}),
        }}
      >
        <div className="p-3.5">
          {/* 标题行:环形倒计时 + 待批工具 + 风险级 */}
          <div className="flex flex-wrap items-center gap-2">
            <span
              role="timer"
              aria-label={`审批倒计时 ${formatClock(ringLeft)}`}
              className="relative flex size-[38px] shrink-0"
            >
              <svg
                viewBox="0 0 38 38"
                width="38"
                height="38"
                aria-hidden="true"
                className="block -rotate-90"
              >
                <circle
                  cx="19"
                  cy="19"
                  r="16"
                  fill="none"
                  stroke="var(--line)"
                  strokeWidth="3"
                  strokeLinecap="round"
                />
                <circle
                  cx="19"
                  cy="19"
                  r="16"
                  pathLength={100}
                  fill="none"
                  stroke={ringColor}
                  strokeDasharray={100}
                  strokeDashoffset={
                    ((1 - ringLeft / timeoutSeconds) * 100).toFixed(2)
                  }
                  style={{
                    transition: "stroke-dashoffset 1s linear, stroke 300ms",
                  }}
                />
              </svg>
              <span
                className={`absolute inset-0 grid place-items-center font-mono text-[10px] tracking-[0.01em] tabular-nums ${ringTextClass}`}
              >
                {formatClock(ringLeft)}
              </span>
            </span>
            <span className="text-[14px] font-medium text-ink">高危调用待审批</span>
            <span className="inline-flex h-5.5 items-center rounded-chip bg-field px-1.5 font-mono text-[11.5px] text-ink-2 shadow-hairline">
              {toolName}
            </span>
            {riskClass && (
              <span
                className={`inline-flex h-5.5 items-center rounded-chip bg-field px-1.5 font-mono text-[11.5px] shadow-hairline ${
                  riskClass === "execute" ? "text-red" : "text-orange"
                }`}
              >
                {riskClass}
              </span>
            )}
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
          {/* 待批内容:write 类带 diff 行按改动本身预览,其余整段内容 */}
          <div className="mt-2.5">
            {diff && diff.length > 0 ? (
              <CodeBlock
                variant="Diff"
                diff={toDiffRows(diff)}
                filename={filename ?? "diff"}
                code={diff.map((line) => line.text).join("\n")}
                labels={{ copy: "复制", copied: "已复制", failed: "复制失败" }}
              />
            ) : (
              <CodeBlock
                variant="Code"
                lines={script.split("\n")}
                filename={filename ?? `${toolName}.txt`}
                labels={{ copy: "复制", copied: "已复制", failed: "复制失败" }}
              />
            )}
          </div>
        </div>

        {/* footer — 三选项;已决出则回显胶囊;触屏断点三按钮平分 44px 命中区 */}
        <div className="flex flex-wrap items-center justify-end gap-1.5 border-t border-line px-3.5 py-2.5">
          {!result ? (
            <>
              <button
                type="button"
                onClick={() => decide("deny")}
                className="flex h-7 items-center rounded-control px-3 text-[12.5px] font-medium text-ink-3 transition-colors duration-100 hover:bg-hover hover:text-ink max-[600px]:h-11 max-[600px]:min-w-0 max-[600px]:flex-1"
              >
                {t.deny}
              </button>
              <button
                type="button"
                onClick={() => decide("once")}
                className="flex h-7 items-center rounded-control bg-surface px-3 text-[12.5px] font-medium text-ink shadow-btn transition-colors duration-100 hover:bg-hover max-[600px]:h-11 max-[600px]:min-w-0 max-[600px]:flex-1"
              >
                {t.allowOnce}
              </button>
              <button
                type="button"
                onClick={() => decide("always")}
                className="flex h-7 items-center rounded-control bg-green-tint px-3 text-[12.5px] font-medium text-green transition-opacity duration-100 hover:opacity-90 max-[600px]:h-11 max-[600px]:min-w-0 max-[600px]:flex-1"
              >
                {t.allowAlways}
              </button>
            </>
          ) : (
            <span
              className={`inline-flex h-6.5 items-center gap-[7px] rounded-full pr-[11px] pl-[5px] text-[12.5px] font-medium ${result.pill}`}
              style={{ animation: "pop-in 260ms cubic-bezier(0.23,1,0.32,1) both" }}
            >
              <span
                className={`flex size-[17px] items-center justify-center rounded-full text-white ${result.dot}`}
              >
                <svg
                  width="10"
                  height="10"
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

        {/* 决定落章(11 票):终局悬盖卡右上,rotate(-12°) 由 stamp-in 终帧持有;
         * once/always 之分交给结果胶囊,章上只落三态;章内署审批信封 seq */}
        {choice && (
          <span
            className={`pointer-events-none absolute top-2.5 right-3.5 grid size-[86px] place-items-center rounded-full border-2 max-[600px]:top-2 max-[600px]:right-2.5 max-[600px]:size-16 ${
              stampOk ? "text-green" : "text-red"
            }`}
            style={{ animation: "stamp-in 340ms cubic-bezier(0.34,1.56,0.64,1) both" }}
          >
            <span className="absolute inset-1 rounded-full border border-current opacity-65" />
            <span className="flex flex-col items-center leading-[1.3]">
              <b className="text-[13.5px] font-semibold tracking-[0.14em] max-[600px]:text-[11px]">
                {stampText}
              </b>
              {typeof requestSeq === "number" && (
                <small className="font-mono text-[8.5px] tracking-[0.04em] opacity-85 max-[600px]:text-[7px]">
                  审计 #{requestSeq}
                </small>
              )}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
