/* 回复 Markdown-lite(12 票):只认围栏码块(```lang)与行内 `code` 两种
 * 语法,标题/列表/加粗不做——终端回复以纯文本+代码为主,少即是多。
 * 全程构建 ReactNode,不走 innerHTML:回复内容可能携带 HTML,一律当
 * 不可信文本处理(React 文本节点自动转义)。流式阶段照原文逐帧出字,
 * 流完由 StreamingText 整体替换为本模块的解析结果(半截围栏不逐帧
 * 解析,不闪畸形)。 */

import type { ReactNode } from "react";
import CodeBlock, { type DiffRow } from "./components/CodeBlock";

const CODE_LABELS = { copy: "复制", copied: "已复制", failed: "复制失败" };

/* 围栏首行的语言名(与操作员原型同款字符集);首行即代码时语言为空 */
const FENCE_LANG = /^([A-Za-z0-9#+._-]*)\n/;

/* 行内 `code` → 等宽 chip(与 SourceChip 同形态:field 底 + hairline) */
function inlineNodes(text: string): ReactNode[] {
  return text.split(/(`[^`\n]+`)/g).map((part, i) => {
    if (i % 2 === 1) {
      return (
        <code
          key={i}
          className="rounded-[4px] bg-field px-[5px] py-[1px] font-mono text-[12px] shadow-hairline [overflow-wrap:anywhere]"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

/* ```diff 围栏:按行首符号分色(+绿 / -红 / @@ 段头弱化),行号从略 */
function diffFenceRows(code: string): DiffRow[] {
  return code.split("\n").map((line) => {
    const head = line.charAt(0);
    const type: DiffRow["type"] =
      head === "+" ? "add" : head === "-" ? "del" : head === "@" ? "hunk" : "ctx";
    return { old: null, cur: null, type, pieces: [{ text: line }] };
  });
}

export function renderMarkdownLite(text: string): ReactNode[] {
  /* 按 ``` 切段:奇数段是代码(首行语言),偶数段是文本 */
  const nodes: ReactNode[] = [];
  text.split("```").forEach((part, i) => {
    if (i % 2 === 1) {
      const matched = FENCE_LANG.exec(part);
      const lang = matched ? matched[1] : "";
      const code = (matched ? part.slice(matched[0].length) : part).replace(
        /\n+$/,
        "",
      );
      nodes.push(
        <div key={`fence-${i}`} className="my-2.5 mb-1">
          {lang === "diff" ? (
            <CodeBlock
              variant="Diff"
              diff={diffFenceRows(code)}
              diffGutter={false}
              filename="diff"
              code={code}
              labels={CODE_LABELS}
            />
          ) : (
            <CodeBlock
              variant="Code"
              lines={code.split("\n")}
              filename={lang || "text"}
              labels={CODE_LABELS}
            />
          )}
        </div>,
      );
    } else {
      const trimmed = part.replace(/^\n+|\n+$/g, "");
      if (trimmed) {
        nodes.push(<span key={`text-${i}`}>{inlineNodes(trimmed)}</span>);
      }
    }
  });
  return nodes;
}
