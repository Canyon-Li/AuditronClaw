"""
基准流水线(bench_pipeline):注入基准与 golden eval 共用的用例处理管线。

每条用例流过六个工位:
    隔离(每用例 from_root 临时工作区,装配期注入)→ 预置材料(setup.write +
    审批门生产同款规则夹具)→ 驱动(会话引擎跑 agent 回合,attended 档位见
    「审批门基准档位」)→ 收集(回合轨迹组装结果 dict)→ 判定(交给各 runner,
    断言语义不同)→ 落盘(JSONL)

职责边界:
- runner 负责"跑哪些用例 + 怎么判定"
- pipeline 负责"一条用例怎么流过去"——agent 是工位上的设备,可替换,管线不动

用法:
    from bench_pipeline import run_case, write_results
    raw = await run_case(case, model, provider)   # 返回 tool_calls/tool_results/reply
"""

import asyncio
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

from auditronclaw.core.approval.gate import (
    ApprovalDecision,
    DecisionSource,
    TurnOrigin,
)
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.session import SessionEngine, TurnEnd

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

RESULTS_DIR = PROJECT_ROOT / "benchmarks" / "results"


# ============ 审计锚定(整场一次) ============

def _ensure_audit_anchor() -> None:
    """把整场基准的审计锚到操作员工作区 logs,不随用例临时目录漂移。

    05 票前靠 reload 链反向保证(切 workspace 前先 import logger 固化
    锚点);reload 链删除后锚定转为 run_case 里的显式装配步。已在别处
    锚定(入口/测试夹具)时静默让位——锚定权归先到者,init_audit_logger
    本身拒绝换址,不得也不需重锚。落点未定时才读 AUDITRONCLAW_WORKSPACE,
    已锚定的进程(测试)不付读 env 的代价。
    """
    from auditronclaw.core.logger import current_audit_log_dir, init_audit_logger
    if current_audit_log_dir() is None:
        init_audit_logger(WorkspaceConfig.from_env().log_dir)


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


def preset_production_rules(workspace: WorkspaceConfig) -> None:
    """把生产同款规则铸进本用例 workspace(经 RuleStore 单一写路径)。

    规则文件落点由装配注入(workspace.approval_rules_file,05 票):
    夹具规则写进用例临时工作区,操作员本地规则文件不被触碰。出处
    bench_fixture 落条目、rule_persisted 入审计——夹具规则的存在本身
    可审计,与"每条豁免可枚举正当性"同一叙事。
    """
    from auditronclaw.core.approval.rules import RuleStore
    store = RuleStore(path=workspace.approval_rules_file)
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


# ============ 邮箱事务台 fixture(零真实网络注入缝) ============

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


# ============ 用例执行 ============

async def run_case(case: dict, model_name: str, provider_name: str,
                   thread_prefix: str = "bench", attended: bool = False) -> dict:
    """执行单条用例,返回原始轨迹(不含任何判定,由各 suite 的 judge 补)。

    每用例隔离(05 票):from_root 临时工作区、装配期注入各工位——不再
    reload 全链,路径绑定天然随用例走。attended 档位(基准应答档位):
    False=无人形态(injection,仅规则放行);True=有人且都批形态
    (golden,未匹配自动批准)。两档都预置生产同款规则。
    """
    case_id = case["id"]
    _ensure_audit_anchor()
    workspace = WorkspaceConfig.from_root(
        tempfile.mkdtemp(prefix=f"{thread_prefix}_{case_id}_"))
    workspace.ensure_dirs()

    # setup: 预置材料(相对 workspace;注入 suite 是恶意材料,golden suite 是良性材料)
    for spec in case.get("setup", {}).get("write", []):
        target = Path(workspace.root) / spec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(spec["content"], encoding="utf-8")

    # setup: 审批门夹具——生产同款规则预置(06 票,两档共有)
    preset_production_rules(workspace)

    # setup: 邮箱事务台用例注入 fixture 邮箱与假 sender(零网络;有 mailbox 键即注入)
    pushes = []
    mailbox_spec = case.get("setup", {}).get("mailbox")
    if mailbox_spec:
        with mailbox_fixture(mailbox_spec, workspace.root) as capture:
            return await _drive_agent(case, workspace, model_name,
                                      provider_name, thread_prefix,
                                      capture.pushes, attended=attended)
    return await _drive_agent(case, workspace, model_name,
                              provider_name, thread_prefix, pushes,
                              attended=attended)


async def _drive_agent(case: dict, workspace: WorkspaceConfig, model_name: str,
                       provider_name: str, thread_prefix: str, pushes: list,
                       attended: bool = False) -> dict:
    """驱动 agent 跑完 trigger 并结构化轨迹(pipeline 的执行工位,与 setup 工位分离)。

    引擎适配器:驱动与事件解析归 SessionEngine(语义钉在 session.py),本函数
    只建 app、跑一个回合,从 turn_end 搭载的回合轨迹组装基准结果 dict——
    形状与语义由表征测试(tests/test_bench_adapter_characterization.py)逐字段钉死。
    工作区装配期注入(05 票):工具/规则/画像落点都出自 workspace。

    attended=True 时以人来源驱动并注入有人且都批应答器(golden 档):打断问人
    与续行仍全在引擎内部,本工位依旧只吃 turn_end,不碰逐事件流。
    """
    case_id = case["id"]
    from auditronclaw.core.agent import create_agent_app

    thread_id = f"{thread_prefix}/{case_id}"
    app = create_agent_app(
        provider_name=provider_name,
        model_name=model_name,
        workspace=workspace,
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
                "workspace": workspace.root,
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
