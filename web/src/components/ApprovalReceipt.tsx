/* 审批回执(07 票):审批卡旁点开见该笔调用的审计条目——经 /api/audit 从
 * 审计旁路取数(04 票),不读 JSONL 档案。非 beautifului 取件,本仓自绘,
 * 视觉语言对齐既有组件(小字、hairline、胶囊)。
 * 10 票(2026-09-02):折叠形态对齐操作员 v2 原型——旋转箭头 toggle +
 * inset 底色块;回执在审批落定后即展开(原型口径),初次渲染即取数,
 * 收起再展开不重复取。
 *
 * 关联口径:approval_requested 条目带完整参数,与审批卡同一份 args 逐字段
 * 对上(键序无关的稳定序列化)即认定是同一笔;其后的首条同工具
 * approval_decision 即这笔的决定;user_persist 的决定再顺延一条
 * rule_persisted(规则是决定的后果)。决定条目在应答落定时才产生——
 * 操作员点选的回显是乐观的,取数重试几拍等它落账,超限如实显示暂未见。 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** 审计旁路条目(/api/audit 返回形状;字段按事件类型增减)。 */
export type AuditEntry = {
  seq: number;
  ts: string;
  thread_id: string;
  event: string;
  tool?: string;
  args?: Record<string, unknown>;
  approved?: boolean;
  source?: string;
  risk_class?: string;
  rule_id?: string;
  rule?: { action?: string; scope?: string; source?: string };
};

const FETCH_RETRY_TIMES = 5;
const FETCH_RETRY_DELAY_MS = 250;

/* 同一 token 的并发取数共享一次 /api/audit 调用:刷新后多张回执同时挂载
 * 只打一发;进行中的等待者拿到同一份最新响应,取数完成即释放(重试各次
 * 重新发起——决定条目要等落账,必须取新)。 */
let inflight: { token: string; promise: Promise<AuditEntry[]> } | null = null;

function fetchAudit(token: string): Promise<AuditEntry[]> {
  if (inflight?.token === token) return inflight.promise;
  const promise = fetch(
    `/api/audit?token=${encodeURIComponent(token)}&limit=200`,
  ).then(async (response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return ((await response.json()) as { entries: AuditEntry[] }).entries;
  });
  promise
    .catch(() => {})
    .finally(() => {
      if (inflight?.promise === promise) inflight = null;
    });
  inflight = { token, promise };
  return promise;
}

/** 键序无关的稳定序列化:比对参数用,不依赖对象键的插入序。 */
function stableStringify(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value))
    return `[${value.map(stableStringify).join(",")}]`;
  const record = value as Record<string, unknown>;
  const keys = Object.keys(record).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`)
    .join(",")}}`;
}

function matchReceipt(entries: AuditEntry[], tool: string, args: Record<string, unknown>) {
  const requested = entries
    .filter(
      (entry) =>
        entry.event === "approval_requested" &&
        entry.tool === tool &&
        stableStringify(entry.args) === stableStringify(args),
    )
    .at(-1);
  if (!requested) return { requested: null, decision: null, persisted: null };
  const decision =
    entries.find(
      (entry) =>
        entry.seq > requested.seq &&
        entry.event === "approval_decision" &&
        entry.tool === tool,
    ) ?? null;
  const persisted =
    decision && decision.approved && decision.source === "user_persist"
      ? (entries.find(
          (entry) => entry.seq > decision.seq && entry.event === "rule_persisted",
        ) ?? null)
      : null;
  return { requested, decision, persisted };
}

function clockOf(ts: string) {
  return ts.slice(11, 19);
}

export default function ApprovalReceipt({
  token,
  tool,
  args,
}: {
  /** 启动 token:REST 端点过门用(首连种下的 cookie 之外再钉一道)。 */
  token: string;
  tool: string;
  args: Record<string, unknown>;
}) {
  const [open, setOpen] = useState(true); // 原型口径:落定即展开
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadingRef = useRef(false);

  const load = useCallback(async () => {
    if (loadingRef.current) return; // 取数中不重复发起(初次与展开兜底可能撞车)
    loadingRef.current = true;
    setError(null);
    try {
      // 决定条目应答落定时才落账:点选回显是乐观的,重试几拍等它
      for (let attempt = 1; attempt <= FETCH_RETRY_TIMES; attempt++) {
        try {
          const list = await fetchAudit(token);
          const matched = matchReceipt(list, tool, args);
          if (attempt === FETCH_RETRY_TIMES || matched.decision) {
            setEntries(
              [matched.requested, matched.decision, matched.persisted].filter(
                (entry): entry is AuditEntry => entry !== null,
              ),
            );
            return;
          }
        } catch (cause) {
          setError(String(cause));
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, FETCH_RETRY_DELAY_MS));
      }
    } finally {
      loadingRef.current = false;
    }
  }, [token, tool, args]);

  /* 初次渲染即取数(默认展开);ref 挡住开发态严格模式的双挂载 */
  const fetchedRef = useRef(false);
  useEffect(() => {
    if (fetchedRef.current) return;
    fetchedRef.current = true;
    void load();
  }, [load]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    // 展开兜底取数:初次取数失败的场合重试;已取到的维持原样
    if (next && entries === null) void load();
  };

  const receipt = entries ? matchReceipt(entries, tool, args) : null;
  const sourceText: Record<string, string> = {
    user_once: "操作员批准(仅本次)",
    user_persist: "操作员批准并永久允许",
    rule_auto: "既定规则自动放行",
    timeout: "等待超时,按拒绝",
    unattended: "无人值守,按拒绝",
  };

  return (
    <div className="text-[11.5px]">
      <button
        type="button"
        aria-expanded={open}
        onClick={toggle}
        className="-ml-2 inline-flex items-center gap-[5px] rounded-control px-2 py-1 font-mono text-[11px] tracking-[0.02em] text-ink-3 transition-colors duration-100 hover:bg-hover-2 hover:text-ink-2"
      >
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
          className="transition-transform duration-200"
          style={{ transform: open ? "rotate(0deg)" : "rotate(-90deg)" }}
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
        审计回执
      </button>
      {open && (
        <div className="mt-1.5 flex flex-col gap-1 rounded-control bg-inset px-3 py-2.5 font-mono text-[11px] leading-[1.7] text-ink-2 shadow-hairline">
          {error && <p className="text-red">回执取数失败:{error}</p>}
          {!error && entries === null && <p className="text-ink-3">取数中…</p>}
          {receipt?.requested && (
            <p className="text-ink-3">
              <span className="text-ink-2">#{receipt.requested.seq}</span>{" "}
              {clockOf(receipt.requested.ts)} approval_requested ·{" "}
              {receipt.requested.risk_class}
            </p>
          )}
          {receipt?.decision && (
            <p className="text-ink-3">
              <span className="text-ink-2">#{receipt.decision.seq}</span>{" "}
              {clockOf(receipt.decision.ts)} approval_decision ·{" "}
              <span className={receipt.decision.approved ? "text-green" : "text-red"}>
                {receipt.decision.approved ? "approved" : "denied"}
              </span>{" "}
              · {sourceText[receipt.decision.source ?? ""] ?? receipt.decision.source}
            </p>
          )}
          {receipt?.persisted && (
            <p className="text-ink-3">
              <span className="text-ink-2">#{receipt.persisted.seq}</span>{" "}
              {clockOf(receipt.persisted.ts)} rule_persisted ·{" "}
              {receipt.persisted.rule?.action} {receipt.persisted.rule?.scope}
            </p>
          )}
          {!error && entries !== null && entries.length === 0 && (
            <p className="text-ink-3">旁路里暂未见这笔的审计条目</p>
          )}
        </div>
      )}
    </div>
  );
}
