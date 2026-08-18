"""
基准共享底座(harness):注入基准与 golden eval 共用的隔离/执行/落盘设施。

职责边界:
- runner 负责"跑哪些用例 + 怎么判定"(断言语义各 suite 不同)
- harness 负责"怎么跑一条用例"(隔离 workspace → 预置材料 → astream 收集轨迹)

用法:
    from harness import run_case, write_results
    raw = await run_case(case, model, provider)   # 返回 tool_calls/tool_results/reply
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

# 项目根加入 sys.path(benchmarks/ 不是包)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"


# ============ 每用例隔离:reload 链 ============

def reload_with_workspace(workspace: str) -> None:
    """
    把 auditronclaw 全链切到指定 workspace。

    reload 顺序敏感:config(路径源头) → sandbox_tools(吃 OFFICE_DIR)
    → builtins(吃 MEMORY_DIR,重建 BUILTIN_TOOLS) → skill_loader(吃 SKILLS_DIR,
    重建全局 _lazy_loader) → agent(from-import 绑定必须最后刷新)。
    链上任何一环漏 reload,都会留下旧 workspace 的路径绑定。
    """
    os.environ["AUDITRONCLAW_WORKSPACE"] = workspace
    import auditronclaw.core.config as cfg
    import auditronclaw.core.tools.sandbox_tools as sb
    import auditronclaw.core.tools.builtins as builtins_mod
    import auditronclaw.core.skill_loader as skill_loader_mod
    import auditronclaw.core.agent as agent_mod
    importlib.reload(cfg)
    importlib.reload(sb)
    importlib.reload(builtins_mod)
    importlib.reload(skill_loader_mod)
    importlib.reload(agent_mod)


# ============ 用例执行 ============

async def run_case(case: dict, model_name: str, provider_name: str,
                   thread_prefix: str = "bench") -> dict:
    """执行单条用例,返回原始轨迹(不含任何判定,由各 suite 的 judge 补)。"""
    case_id = case["id"]
    workspace = tempfile.mkdtemp(prefix=f"{thread_prefix}_{case_id}_")

    reload_with_workspace(workspace)

    # setup: 预置材料(相对 workspace;注入 suite 是恶意材料,golden suite 是良性材料)
    for spec in case.get("setup", {}).get("write", []):
        target = Path(workspace) / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec["content"], encoding="utf-8")

    from auditronclaw.core.agent import create_agent_app

    thread_id = f"{thread_prefix}/{case_id}"
    app = create_agent_app(
        provider_name=provider_name,
        model_name=model_name,
        checkpointer=MemorySaver(),
        thread_id=thread_id,
    )
    config = {"configurable": {"thread_id": thread_id}}

    tool_calls = []      # [{tool, args}]
    tool_results = []    # [{tool, result}] 与 tool_calls 按序对应(ToolNode 串行回填)
    reply_text = []      # 非 tool 消息文本

    inputs = {"messages": [HumanMessage(content=case["trigger"])]}
    async for event in app.astream(inputs, config=config, stream_mode="updates"):
        for _node, node_data in event.items():
            for msg in node_data.get("messages", []):
                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        tool_calls.append({"tool": tc["name"], "args": tc.get("args", {})})
                if getattr(msg, "type", "") == "tool":
                    tool_results.append({"tool": msg.name, "result": str(msg.content)})
                elif msg.content:
                    reply_text.append(str(msg.content))

    return {
        "case_id": case_id,
        "surface": case["surface"],
        "workspace": workspace,
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "reply": "\n".join(reply_text),
    }


# ============ 结果落盘 ============

def write_results(results: list, summary: dict, suite: str) -> Path:
    """JSONL + summary 落盘到时间戳目录,返回目录路径。"""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / f"{suite}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n",
        encoding="utf-8",
    )
    summary = {**summary, "suite": suite}
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return out_dir
