/* 组件取自 beautifului.dev(https://www.beautifului.dev/),站页 copy-paste 分发
 * 组件名 Tool Chips · 取用日期 2026-09-01 · MIT · Copyright (c) 2026 Shane Levine
 * 本仓改动:取件 + 操作员原型改造(2026-09-02)——撤掉站方画廊的逐行 700ms
 * 逐格演示(真实流里行即时呈现,入场动画保留);详情行改为换行呈现不截断
 * (参数 JSON 需完整可读);触屏断点行高 34px。
 * 13 票(2026-09-02 第四轮,操作员最新版设计):行首图标常驻(撤取件的
 * 悬停换形),开合指示改行尾常驻 chev,与分组头/历史折叠/回执同一语言;
 * 详情区左界竖线改 ⎿ 回钩(绝对定位、恒最淡墨,不随行染色)。
 * 2026-09-25:砍取件的 diff 芯片/悬停预览 portal、演示数据与未接线的
 * 回调形参——终端唯一调用方只用工具行分组,组件收敛为工具轨迹本体。 */

"use client";

import { useState } from "react";

const Icons: Record<string, React.ReactNode> = {
  think: <path d="M12 2l2.4 7.2L22 12l-7.6 2.8L12 22l-2.4-7.2L2 12l7.6-2.8z" />,
  write: <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5z" /></g>,
  run: <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 17l6-5-6-5M12 19h8" /></g>,
  read: <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6" /></g>,
};

export type ToolDetailLine = { text: string; tone?: "add" };

export type ToolStep = {
  icon: string;
  label: string;
  chip: string;
  mono: boolean;
  detailMono: boolean;
  detail: ToolDetailLine[];
};

export default function ToolChips({
  steps,
  header,
}: {
  steps: ToolStep[];
  /** 分组头文案(调用方按事件流推导,如 "本轮 N 次工具调用")。 */
  header: string;
}) {
  const [open, setOpen] = useState(true);
  const [openRows, setOpenRows] = useState<Set<string>>(new Set());

  const toggleRow = (label: string) =>
    setOpenRows((current) => {
      const next = new Set(current);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  return (
    <div className="w-full pb-1">
      {/* collapsed run header */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
        className="-mx-1.5 flex w-fit items-center gap-1.5 rounded-control px-1.5 py-1 text-[12.5px] text-ink-2 transition-colors duration-100 hover:bg-hover-2"
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="transition-transform duration-200" style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }}>
          <path d="M6 9l6 6 6-6" />
        </svg>
        <span className="tabular-nums">{header}</span>
      </button>

      {/* tool call rows */}
      <div className="grid transition-[grid-template-rows,opacity] duration-300" style={{ gridTemplateRows: open ? "1fr" : "0fr", opacity: open ? 1 : 0 }}>
        {/* -mx-1 + px-1.5 keeps content at the same x while giving the
            row hover pills room inside this overflow-hidden clip box */}
        <div className="-mx-1 overflow-hidden px-1.5 pb-1">
          <div className="mt-1.5 flex flex-col gap-1">
            {steps.map((row) => {
              const rowOpen = openRows.has(row.label);
              return (
              <div key={row.label} style={{ animation: "fade-up 300ms cubic-bezier(0.23,1,0.32,1) both" }}>
                <button
                  type="button"
                  aria-expanded={rowOpen}
                  onClick={() => toggleRow(row.label)}
                  className="-mx-[3px] flex h-7 w-[calc(100%+6px)] min-w-0 items-center gap-2 rounded-control px-[3px] text-left transition-colors duration-100 hover:bg-hover-2 max-[600px]:h-8.5"
                >
                  <span className="grid size-4 shrink-0 place-items-center text-ink-3">
                    <svg
                      width="13" height="13" viewBox="0 0 24 24" fill={row.icon === "think" ? "currentColor" : "none"} stroke="currentColor"
                    >
                      {Icons[row.icon]}
                    </svg>
                  </span>
                  <span className="shrink-0 text-[12.5px] font-medium text-ink">{row.label}</span>
                  <span
                    className={`inline-flex h-5.5 min-w-0 flex-1 cursor-pointer items-center truncate rounded-chip bg-field px-1.5
                    text-[11.5px] text-ink-2 shadow-hairline transition-colors duration-100 hover:bg-hover-2
                    ${row.mono ? "font-mono" : ""}`}
                  >
                    {row.chip}
                  </span>
                  {/* 行尾常驻 chev(13 票):整行可点的常驻字形,chip 是 flex-1
                      使之贴右缘;旋转语义与分组头/历史折叠/回执一致 */}
                  <span className="grid size-4 shrink-0 place-items-center text-ink-3">
                    <svg
                      width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"
                      className="transition-transform duration-200"
                      style={{ transform: rowOpen ? "rotate(0deg)" : "rotate(-90deg)" }}
                    >
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </span>
                </button>

                {/* expanded detail */}
                <div
                  className="grid transition-[grid-template-rows,opacity] duration-300"
                  style={{ gridTemplateRows: rowOpen ? "1fr" : "0fr", opacity: rowOpen ? 1 : 0, transitionTimingFunction: "cubic-bezier(0.23, 1, 0.32, 1)" }}
                >
                  <div className="min-h-0 overflow-hidden">
                    <div className="relative mt-0.5 mb-1 ml-2 flex flex-col gap-0.5 py-0.5 pl-3.5">
                      {/* ⎿ 回钩(13 票):结果行从属于动作行的记号。绝对定位
                          不占行内排版(不参与 anywhere 折行计算);色恒最淡
                          墨——钩标从属,不随行增减染色 */}
                      <span aria-hidden="true" className="absolute top-0.5 left-px font-mono text-[11px] leading-[1.65] text-ink-3">
                        ⎿
                      </span>
                      {row.detail.map((line, index) => (
                        <span
                          key={`${index}:${line.text}`}
                          className={`text-[11.5px] leading-[1.6] [overflow-wrap:anywhere] ${row.detailMono ? "font-mono" : ""} ${line.tone === "add" ? "text-green" : "text-ink-2"}`}
                        >
                          {line.text}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
