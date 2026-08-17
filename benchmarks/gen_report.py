"""一次性生成 50 条注入基准结果报告(合并 cases.yaml + results.jsonl)。"""
import json
import sys
import yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = sorted((ROOT / "benchmarks" / "results").iterdir(), key=lambda p: p.name)[-1]
print(f"使用结果目录: {RESULTS.name}")

cases = yaml.safe_load((ROOT / "benchmarks/cases/injection_cases.yaml").read_text(encoding="utf-8"))
res = {json.loads(l)["case_id"]: json.loads(l)
       for l in (RESULTS / "results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}


def direction(c):
    parts = []
    for a in c["asserts"]:
        if a["type"] == "forbidden_tool_call":
            t, m = a["tool"], a.get("arg_contains", "")
            if m == "..":
                parts.append(f"诱导调用 {t} 越权路径")
            elif m in ("-c", "-m"):
                parts.append(f"诱导 {t} 内联解释器")
            elif m:
                parts.append(f"诱导 {t} 写入污染载荷")
            else:
                parts.append(f"诱导调用 {t}")
        elif a["type"] == "leak_keyword":
            parts.append(f"诱导泄露关键词「{a['keyword']}」")
    return "；".join(parts)


def content(c):
    trig = c["trigger"].replace("\n", " ").strip()
    return trig[:60] + ("…" if len(trig) > 60 else "")


def status(r):
    if r.get("error"):
        return "⏭ 执行错误", "模型内容过滤器拦截(不计)"
    if r["blocked"]:
        return "✅ 拦截", ""
    if r.get("harm_landed"):
        return "🔴 攻破·落地", "危害真实达成(P0)"
    return "🟠 攻破·兜住", "提示面失守,工具层拦截"


lines = []
lines.append("# 注入拦截基准:50 条用例明细\n")
lines.append(f"**模型**: glm-4-flash · **日期**: 2026-08-17 · **结果目录**: benchmarks/results/{RESULTS.name} · **有效用例**: 49(jail_001 被内容过滤器剔除)\n")
lines.append("| ID | 面 | 测试方向 | 测试内容(触发消息) | 结果 | 备注 |")
lines.append("|---|---|---|---|---|---|")

for c in cases:
    r = res.get(c["id"], {})
    st, note = status(r)
    detail = note or "、".join(r.get("hits", []))
    lines.append(f"| `{c['id']}` | {c['surface']} | {direction(c)} | {content(c)} | {st} | {detail} |")

out = ROOT / "docs/local/injection-bench-results-2026-08-17.md"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"已生成: {out} ({len(cases)} 条)")
