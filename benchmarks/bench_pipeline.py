"""
基准流水线(bench_pipeline):注入基准与 golden eval 共用的用例处理管线。

每条用例流过六个工位:
    隔离(reload 链切 workspace)→ 预置材料(setup.write)→ 驱动(astream 跑 agent)
    → 收集(轨迹结构化)→ 判定(交给各 runner,断言语义不同)→ 落盘(JSONL)

职责边界:
- runner 负责"跑哪些用例 + 怎么判定"
- pipeline 负责"一条用例怎么流过去"——agent 是工位上的设备,可替换,管线不动

用法:
    from bench_pipeline import run_case, write_results
    raw = await run_case(case, model, provider)   # 返回 tool_calls/tool_results/reply
"""

import asyncio
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager

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

# 邮箱事务台注入缝(接缝 B 的基准侧):fixture 邮箱 + 假 sender + 占位凭据。
# 占位凭据骗过工具层"未配置不碰网络"的前置检查——离开本上下文即还原生产通道。
_BENCH_MAIL_ENV = {
    "MAIL_ACCOUNT": "bench@fixture.local",
    "MAIL_IMAP_PASSWORD": "bench-placeholder",
    "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/bench-fixture-webhook",
}


@contextmanager
def mailbox_fixture(spec: dict, workspace: str):
    """
    注入邮箱事务台的测试通道:邮件从 fixture 文件读,推送进捕获列表,零真实网络。

    spec 为用例 setup.mailbox({mails: [{sender,subject,hours_ago,body}]}),
    hours_ago 在运行期换算 ISO 日期——用例不写绝对日期,任何时刻跑都在窗口内。
    yield 捕获器(pushes 属性 = 已推送文本列表);退出时还原传输层与环境变量。
    """
    import auditronclaw.core.tools.mail_tool as mail_tool
    import auditronclaw.core.tools.feishu_tool as feishu_tool

    fixture_path = os.path.join(workspace, "bench_mailbox.json")
    now = datetime.now()
    raw = [{
        "sender": m["sender"],
        "subject": m["subject"],
        "date": (now - timedelta(hours=m["hours_ago"])).isoformat(),
        "body": m["body"],
    } for m in spec["mails"]]
    Path(fixture_path).write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    class _Capture:
        def __init__(self):
            self.pushes = []

        def fake_sender(self, webhook_url, payload):
            self.pushes.append(payload["content"]["text"])
            return {"code": 0, "msg": "success"}

    capture = _Capture()
    saved_env = {k: os.environ.get(k) for k in _BENCH_MAIL_ENV}
    os.environ.update(_BENCH_MAIL_ENV)
    mail_tool.set_provider(mail_tool.load_fixture_provider(fixture_path))
    feishu_tool.set_sender(capture.fake_sender)
    try:
        yield capture
    finally:
        mail_tool.set_provider(None)
        feishu_tool.set_sender(None)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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

    # setup: 邮箱事务台用例注入 fixture 邮箱与假 sender(零网络;有 mailbox 键即注入)
    pushes = []
    mailbox_spec = case.get("setup", {}).get("mailbox")
    if mailbox_spec:
        with mailbox_fixture(mailbox_spec, workspace) as capture:
            return await _drive_agent(case, workspace, model_name,
                                      provider_name, thread_prefix, capture.pushes)
    return await _drive_agent(case, workspace, model_name,
                              provider_name, thread_prefix, pushes)


async def _drive_agent(case: dict, workspace: str, model_name: str,
                       provider_name: str, thread_prefix: str, pushes: list) -> dict:
    """驱动 agent 跑完 trigger 并结构化轨迹(pipeline 的执行工位,与 setup 工位分离)。"""
    case_id = case["id"]
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
        "pushes": pushes,
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
