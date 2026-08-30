"""
基准流水线(bench_pipeline):注入基准与 golden eval 共用的用例处理管线。

每条用例流过六个工位:
    隔离(reload 链切 workspace)→ 预置材料(setup.write + 审批门生产同款规则夹具)
    → 驱动(会话引擎跑 agent 回合,attended 档位见「审批门基准档位」)
    → 收集(回合轨迹组装结果 dict)→ 判定(交给各 runner,断言语义不同)→ 落盘(JSONL)

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

from langgraph.checkpoint.memory import MemorySaver

# 项目根加入 sys.path(benchmarks/ 不是包)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# session 不在 reload 链上(收 app 为参数、不 import agent),顶层 import 的
# 绑定不会被 reload 刷新;agent 仍须函数内 import 才能拿到重载后的新绑定。
# approval.gate 同理不在链上(枚举与载荷类型全程同一对象,engine 与门共用)。
from auditronclaw.core.approval.gate import (
    ApprovalDecision,
    DecisionSource,
    TurnOrigin,
)
from auditronclaw.core.session import SessionEngine, TurnEnd

# 审计锚定:logger 单例在其模块导入时即构造,必须赶在首个 reload 把 config
# 切去临时 workspace 之前导入——否则整场基准的审计会被首用例的临时目录锚走
# (位置随场而变、临时目录会被系统清理),与"审计落 WORKSPACE_DIR/logs"相悖。
import auditronclaw.core.logger  # noqa: F401  导入即固化锚点,不使用绑定

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"


# ============ 每用例隔离:reload 链 ============

def reload_with_workspace(workspace: str) -> None:
    """
    把 auditronclaw 全链切到指定 workspace。

    reload 顺序敏感:config(路径源头) → approval.rules(吃 APPROVAL_RULES_FILE,
    06 票夹具规则随每用例 workspace——漏 reload 会锚死在仓库 workspace,
    操作员本地规则串进基准) → sandbox_tools(吃 OFFICE_DIR)
    → builtins(吃 MEMORY_DIR,重建 BUILTIN_TOOLS) → skill_loader(吃 SKILLS_DIR,
    重建全局 _lazy_loader) → agent(from-import 绑定必须最后刷新)。
    链上任何一环漏 reload,都会留下旧 workspace 的路径绑定。
    """
    os.environ["AUDITRONCLAW_WORKSPACE"] = workspace
    import auditronclaw.core.config as cfg
    import auditronclaw.core.approval.rules as approval_rules_mod
    import auditronclaw.core.tools.sandbox_tools as sb
    import auditronclaw.core.tools.builtins as builtins_mod
    import auditronclaw.core.skill_loader as skill_loader_mod
    import auditronclaw.core.agent as agent_mod
    importlib.reload(cfg)
    importlib.reload(approval_rules_mod)
    importlib.reload(sb)
    importlib.reload(builtins_mod)
    importlib.reload(skill_loader_mod)
    importlib.reload(agent_mod)


# ============ 审批门基准档位(06 票:基准应答档位) ============

# 生产同款规则 = 生产冷启动清单(tasks.json 写、画像写、scripts/ 执行):
# 两档基准都预置。injection 档在此基础上无人值守——攻击的新颖写
# (office 根目录脚本)与执行无规则可乘,jail_010 家族断在门上;
# golden 档另配未匹配自动批准应答器(有人且都批形态)——over_refusal
# 度量"门不挡合法流",不度量审批摩擦。守恒由测试钉死(集合并 =
# 冷启动清单,防悄悄漂移让两档数字失去可比性)。
PRODUCTION_RULE_FIXTURES = (
    ("execute", "office/scripts/**"),
    ("write", "tasks.json"),
    ("write", "memory/profiles/**"),
)


def preset_production_rules() -> None:
    """把生产同款规则铸进本用例 workspace(经 RuleStore 单一写路径)。

    须在 reload_with_workspace 之后调用:规则文件路径随重载指向当前用例的
    临时 workspace。出处 bench_fixture 落条目、rule_persisted 入审计——
    夹具规则的存在本身可审计,与"每条豁免可枚举正当性"同一叙事。
    """
    from auditronclaw.core.approval.rules import RuleStore
    store = RuleStore()
    for action, scope in PRODUCTION_RULE_FIXTURES:
        store.persist_rule(action=action, scope=scope, source="bench_fixture",
                           thread_id="bench_fixture")


def _approve_all(_request) -> ApprovalDecision:
    """golden 档应答器:有人且都批——未匹配规则的高危一律批准一次。

    persist=False 不铸规则:生产规则形状不因基准漂移,决定留痕
    source=user_once(审批摩擦不是 golden 度量的对象)。
    """
    return ApprovalDecision(approved=True, persist=False,
                            source=DecisionSource.USER_ONCE)


# summary 的 approval_form 取值(档位概念住在流水线,runner 引用防字面量漂移)
APPROVAL_FORM_UNATTENDED = "unattended"
APPROVAL_FORM_ATTENDED_AUTO_APPROVE = "attended_auto_approve"


# ============ 邮箱事务台 fixture(注入点,零真实网络) ============

# 邮箱事务台注入点(注入点 B 的基准侧):fixture 邮箱 + 假 sender + 占位凭据。
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


# ============ 用例执行 ============

async def run_case(case: dict, model_name: str, provider_name: str,
                   thread_prefix: str = "bench", attended: bool = False) -> dict:
    """执行单条用例,返回原始轨迹(不含任何判定,由各 suite 的 judge 补)。

    attended 档位(基准应答档位):False=无人形态(injection,仅规则放行);
    True=有人且都批形态(golden,未匹配自动批准)。两档都预置生产同款规则。
    """
    case_id = case["id"]
    workspace = tempfile.mkdtemp(prefix=f"{thread_prefix}_{case_id}_")

    reload_with_workspace(workspace)

    # setup: 预置材料(相对 workspace;注入 suite 是恶意材料,golden suite 是良性材料)
    for spec in case.get("setup", {}).get("write", []):
        target = Path(workspace) / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec["content"], encoding="utf-8")

    # setup: 审批门夹具——生产同款规则预置(06 票,两档共有)
    preset_production_rules()

    # setup: 邮箱事务台用例注入 fixture 邮箱与假 sender(零网络;有 mailbox 键即注入)
    pushes = []
    mailbox_spec = case.get("setup", {}).get("mailbox")
    if mailbox_spec:
        with mailbox_fixture(mailbox_spec, workspace) as capture:
            return await _drive_agent(case, workspace, model_name,
                                      provider_name, thread_prefix,
                                      capture.pushes, attended=attended)
    return await _drive_agent(case, workspace, model_name,
                              provider_name, thread_prefix, pushes,
                              attended=attended)


async def _drive_agent(case: dict, workspace: str, model_name: str,
                       provider_name: str, thread_prefix: str, pushes: list,
                       attended: bool = False) -> dict:
    """驱动 agent 跑完 trigger 并结构化轨迹(pipeline 的执行工位,与 setup 工位分离)。

    引擎适配器:驱动与事件解析归 SessionEngine(语义钉在 session.py),本函数
    只建 app、跑一个回合,从 turn_end 搭载的回合轨迹组装基准结果 dict——
    形状与语义由表征测试(tests/test_bench_adapter_characterization.py)逐字段钉死。

    attended=True 时以人来源驱动并注入有人且都批应答器(golden 档):打断问人
    与续行仍全在引擎内部,本工位依旧只吃 turn_end,不碰逐事件流。
    """
    case_id = case["id"]
    from auditronclaw.core.agent import create_agent_app

    thread_id = f"{thread_prefix}/{case_id}"
    app = create_agent_app(
        provider_name=provider_name,
        model_name=model_name,
        checkpointer=MemorySaver(),
        thread_id=thread_id,
    )

    engine = SessionEngine(app, thread_id,
                           approval_responder=_approve_all if attended else None)
    # 基准是批处理消费者:只吃 turn_end 搭载的聚合轨迹;逐事件流归交互式
    # 适配器(TUI/Web),本工位不消费。缺省无人形态:来源不声明即无人,
    # 门构造上不问人(规则未命中直接拒)
    async for event in engine.run_turn(
            case["trigger"],
            origin=TurnOrigin.HUMAN if attended else TurnOrigin.UNATTENDED):
        if isinstance(event, TurnEnd):
            trajectory = event.trajectory
            return {
                "case_id": case_id,
                "surface": case["surface"],
                "workspace": workspace,
                "tool_calls": trajectory.tool_calls,
                "tool_results": trajectory.tool_results,
                "reply": trajectory.reply,
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
