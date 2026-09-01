/* 组件取自 beautifului.dev(https://www.beautifului.dev/),站页 copy-paste 分发
 * 组件名 Prompt Bar · 取用日期 2026-09-01 · MIT · Copyright (c) 2026 Shane Levine
 * 本仓改动:取件 + 操作员原型改造(2026-09-02)——对齐 v2 原型的停靠式输入条:
 * 裁掉 @ 数据源菜单、/ 命令菜单、附件与品牌标记(引擎只认 /exit /quit,菜单插文
 * 引擎不识别)及自动演示与宽行换排逻辑;保留自增高 textarea、Enter 发送 /
 * Shift+Enter 换行(含输入法合成态判定)与发送钮形态。 */

"use client";

import { useLayoutEffect, useRef, useState } from "react";

/* 原型 .composer textarea 的最大高度(自增高到 132px 后内部滚动) */
const MAX_HEIGHT_PX = 132;

export default function PromptBar({
  placeholder,
  onSend,
}: {
  placeholder?: string;
  onSend?: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  /* 自增高:内容决定高度,封顶后在框内滚动 */
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "0px";
    input.style.height = `${Math.min(input.scrollHeight, MAX_HEIGHT_PX)}px`;
  }, [draft]);

  const canSend = draft.trim().length > 0;
  const send = () => {
    if (!canSend) return;
    onSend?.(draft.trim());
    setDraft("");
  };

  return (
    <div
      className="flex items-end gap-2 rounded-window bg-surface p-1 pl-2.5 shadow-card
        transition-shadow duration-150 focus-within:shadow-[0_0_0_1.5px_var(--line-strong),0_4px_12px_oklch(0.24_0.01_258/0.08),0_16px_40px_oklch(0.24_0.01_258/0.1)]"
    >
      <textarea
        ref={inputRef}
        rows={1}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            !event.shiftKey &&
            !event.nativeEvent.isComposing
          ) {
            event.preventDefault();
            send();
          }
        }}
        placeholder={placeholder ?? "输入消息,回车发送…"}
        aria-label="输入消息"
        className="max-h-33 min-h-8.5 w-full min-w-0 flex-1 resize-none border-0 bg-transparent
          py-2.25 text-[13.5px] leading-[1.55] tracking-[-0.005em] text-ink outline-none
          [overflow-wrap:anywhere] placeholder:text-ink-3"
      />
      <button
        type="button"
        aria-label="发送"
        disabled={!canSend}
        onClick={send}
        className="grid size-8 shrink-0 place-items-center rounded-control bg-surface
          text-ink shadow-btn transition-colors duration-100 hover:bg-hover
          active:translate-y-px disabled:text-ink-3 max-[600px]:size-10"
      >
        <svg
          width="15"
          height="15"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M12 19V5" />
          <path d="M5 12l7-7 7 7" />
        </svg>
      </button>
    </div>
  );
}
