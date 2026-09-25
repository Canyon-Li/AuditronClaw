/* 组件取自 beautifului.dev(https://www.beautifului.dev/),站页 copy-paste 分发
 * 组件名 Streaming Text · 取用日期 2026-09-01 · MIT · Copyright (c) 2026 Shane Levine
 * 本仓改动:取件 + 操作员原型改造(2026-09-02)——正文与光标对齐 v2 原型的
 * 回复形态(13.5px/1.7、光标闪烁)。同日间隙修复(11 票):去掉取件给英文
 * 逐词流硬加的词后空格——本仓分帧保留源空格,该空格在中文两字一帧的
 * 场景成了字间假空隙;正文改 whitespace-pre-wrap,原文空格与换行如实呈现,
 * 不再被默认空白折叠改写。12 票:流完整体替换为 Markdown-lite 渲染结果
 * (围栏码块/行内码/```diff 分色,见 markdownLite)——流式阶段照原文
 * 逐帧出字带光标,半截围栏不逐帧解析;容器 p→div 以嵌块级码块。
 * 2026-09-25:砍取件的来源/跟进/操作行与循环回放设施——终端唯一调用方
 * 恒传空,组件收敛为正文流本体(零行为变化)。 */

"use client";

import { useEffect, useState } from "react";
import { renderMarkdownLite } from "../markdownLite";

const WORD_MS = 55;

/** 逐帧上屏的词片(空白切词、中日韩两字一帧,分帧见 TerminalPage)。 */
export type StreamingToken = { text: string };

export default function StreamingText({
  content,
}: {
  content: StreamingToken[];
}) {
  const [count, setCount] = useState(0);
  const done = count >= content.length;

  useEffect(() => {
    if (done) return;
    const t = setTimeout(() => setCount((c) => c + 1), WORD_MS);
    return () => clearTimeout(t);
  }, [count, done]);

  return (
    <div className="w-full">
      <div className="text-pretty text-[13.5px] leading-[1.7] whitespace-pre-wrap text-ink max-[600px]:text-[13px]">
        {done ? (
          /* 流完:整体替换为 Markdown-lite 解析结果(码块/行内码此刻成形) */
          renderMarkdownLite(content.map((token) => token.text).join(""))
        ) : (
          <>
            {content.slice(0, count).map((token, i) => (
              <span key={i} className="inline">
                {token.text}
              </span>
            ))}
            <span
              className="ml-0.5 inline-block h-[15px] w-[7px] translate-y-[-2px] rounded-[1px] bg-ink-2"
              style={{ animation: "blink 1s steps(2) infinite" }}
            />
          </>
        )}
      </div>
    </div>
  );
}
