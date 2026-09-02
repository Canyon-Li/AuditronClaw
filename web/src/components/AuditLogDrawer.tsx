/* 本仓自绘(非 beautifului 取件):审计日志视图(操作员 v2 原型 2026-09-02
 * 第四轮)——页头「审计日志」入口拉出的会话事件流实时镜像。数据不另起
 * 账本:useTerminalStream 的 envelopes 即逐帧原文(主视图按语义分组呈现,
 * 心跳等被过滤的后台帧在此如实可见,与页头「后台 n 帧」可互证),渲染
 * 就是逐帧 JSON.stringify 直读;后端重启画面重建时此处同源同步。
 * 关闭三路:×钮、点背板、Esc(监听器随开合挂卸,关着不占全局键位)。 */

"use client";

import { useEffect, useRef } from "react";
import type { Envelope } from "../protocol";

export default function AuditLogDrawer({
  envelopes,
  open,
  onClose,
}: {
  /** 本连接已见的全部流帧(含主视图过滤掉的心跳回合),逐帧直读。 */
  envelopes: Envelope[];
  open: boolean;
  onClose: () => void;
}) {
  const preRef = useRef<HTMLPreElement>(null);

  /* 开着时帧落地即贴底(与原型同口径:帧到即跟随到底);初次打开也在
   * 渲染后贴到最新帧 */
  useEffect(() => {
    if (!open) return;
    const pre = preRef.current;
    if (pre) pre.scrollTop = pre.scrollHeight;
  }, [open, envelopes.length]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-70 grid place-items-center bg-[color-mix(in_oklab,var(--ink)_26%,transparent)] p-5"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-label="审计日志 · main.jsonl"
        className="flex max-h-[min(76vh,660px)] w-[min(840px,100%)] flex-col overflow-hidden rounded-window bg-surface shadow-overlay"
        style={{ animation: "fade-up 300ms cubic-bezier(0.23,1,0.32,1) both" }}
      >
        <div className="flex items-center justify-between gap-2.5 border-b border-line py-2 pr-2 pl-4">
          <span className="inline-flex items-center gap-2 font-mono text-[12px] text-ink-2">
            审计日志 · main.jsonl
            <span className="rounded-chip bg-field px-[7px] py-[1px] text-[10.5px] text-ink-3 shadow-hairline">
              实时镜像
            </span>
          </span>
          <button
            type="button"
            aria-label="关闭审计日志"
            onClick={onClose}
            className="grid size-6.5 place-items-center rounded-chip text-ink-3 transition-colors duration-100 hover:bg-hover-2 hover:text-ink"
          >
            <svg
              width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            >
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
        <pre
          ref={preRef}
          className="min-h-0 flex-1 overflow-auto overscroll-contain px-4 py-3 font-mono text-[11px] leading-[1.7] whitespace-pre text-ink-2"
        >
          {envelopes.map((frame) => JSON.stringify(frame)).join("\n")}
        </pre>
        <p className="border-t border-line-soft px-4 py-1.5 text-[11px] text-ink-3">
          会话内每个行为落一行,主视图只呈摘要;存档与此同口径。
        </p>
      </div>
    </div>
  );
}
