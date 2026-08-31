"""改造前基线（登记面收窄与域包模板改造）：装配工具集合 + 分级快照。

改造的验收标准是"语义不变"——装配出的工具集合与每个工具的分级结果
逐项等于改造前。本模块把改造前事实采集入库作夹具（tests/baseline/），
对照不靠 git 考古与记忆；改造收口时在装配点重跑同一套采样断言相等。

快照签名：(工具, 固定样例参数, 域名门分支) → (级别, targets)。
- 样例参数固定写死在下方两张表里，改造两侧共用，改样例即改两侧；
- 绑定域工具（条件分级）取两个分支：默认名单 / 绑定域被挤出名单；
- shell 工具按命令段判定，样例命令覆盖全纯读 / 解释器 / 重定向 /
  写段 / 混合段；
- 技能与外接工具设计上 unclassified（走 fail-closed 默认必批），不拍
  分级快照，只进集合等值检查；默认装配两者皆空，集合夹具如实记录。

重建夹具（改造前一次性，勿在改造中途重写——那等于移动基尺）：
    python tests/test_pre_refactor_baseline.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from helpers import production_builtin_tools

from auditronclaw.core.approval.classifier import classify_tool_call
from auditronclaw.core.config import WorkspaceConfig
from auditronclaw.core.tools import domain_gate

BASELINE_DIR = os.path.join(os.path.dirname(__file__), "baseline")
TOOL_SET_FIXTURE = os.path.join(BASELINE_DIR, "pre_refactor_tool_set.json")
CLASSIFICATION_FIXTURE = os.path.join(BASELINE_DIR, "pre_refactor_classification.json")

# 绑定域工具的域名门分支值（快照条目的 domain_gate 字段）：
# default = 默认名单当刻内容（两个绑定域都在名单内）
# bound_domain_removed = 把绑定域挤出名单（默认名单清空、环境扩展清空、
#   规则文件指向不存在路径）——挤的是"本条工具绑定的域"，非全局同名态
BRANCH_DEFAULT = "default"
BRANCH_BOUND_DOMAIN_REMOVED = "bound_domain_removed"

# 非条件工具的固定样例参数（分级判定吃参数的只有写目标提取与 shell 命令）
TOOL_SAMPLE_ARGS = {
    "get_current_time": {},
    "calculator": {"expression": "2**10"},
    "save_user_profile": {"new_content": "用户偏好:回复保持简洁"},
    "list_office_files": {"sub_dir": "notes"},
    "read_office_file": {"filepath": "notes/a.txt"},
    "write_office_file": {"filepath": "notes/a.txt", "content": "hello", "mode": "w"},
    "get_system_model_info": {},
    "list_scheduled_tasks": {},
    "schedule_task": {"target_time": "2027-01-01 08:00:00",
                      "description": "发日报", "repeat": "daily", "repeat_count": 3},
    "modify_scheduled_task": {"task_id": "abc12345", "new_description": "发周报"},
    "delete_scheduled_task": {"task_id": "abc12345"},
    "submit_mailbox_desk_report": {"window_hours": 24, "total_mails": 5,
                                   "todos": ["回复张三的报价邮件"],
                                   "needs_reply": [], "notices": [], "ignorable_count": 0},
    # 绑定域工具（条件分级）：两个分支各拍一条
    "send_feishu_summary": {"summary_text": "今日共 5 封邮件，待办 1 项"},
    "read_recent_emails": {"hours": 24, "max_emails": 10},
}

# shell 样例命令（快照签名里的"固定样例参数"对本工具即命令串）：
# 全纯读 / 解释器段 / 重定向 / 写段 / 混合段（读+删，整条按删）
SHELL_SAMPLE_COMMANDS = [
    "ls && cat notes/a.txt",
    "python scripts/run.py",
    "echo hi > out.txt",
    "mkdir reports",
    "cat a.txt && rm old.txt",
]


def assembled_tool_names() -> list:
    """默认装配的工具名（装配顺序）：内置工厂产物 + 技能（默认空工作区为零）。

    技能工具随工作区内容装配、外接工具由调用方注入，两者都不在默认
    装配里——集合等值检查的覆盖边界与夹具 notes 一致。
    03 票 feishu 迁域后，生产装配点经 build_builtin_tools 参数注入域工具
    （插回迁移前原位）——采样走共享的生产同款装配（helpers）；夹具本身不动
    （语义不变即顺序不变）。
    """
    workspace = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="baseline_ws_"))
    try:
        workspace.ensure_dirs()
        return [t.name for t in production_builtin_tools(workspace, "baseline_probe")]
    finally:
        shutil.rmtree(workspace.root, ignore_errors=True)


@contextmanager
def domain_gate_branch(branch: str):
    """把域名门钉到快照要求的分支（退出还原，先例 test_domain_extension）。

    环境变量扩展一律摘除（快照不依赖部署机的 AUDITRONCLAW_ALLOWED_DOMAINS），
    patch.dict 快照还原，不裸改进程环境。
    """
    if branch not in (BRANCH_DEFAULT, BRANCH_BOUND_DOMAIN_REMOVED):
        raise ValueError(f"未知域名门分支: {branch}")
    env_without_domain_extensions = {
        k: v for k, v in os.environ.items()
        if k != "AUDITRONCLAW_ALLOWED_DOMAINS"}
    with patch.dict(os.environ, env_without_domain_extensions, clear=True):
        if branch == BRANCH_DEFAULT:
            yield
            return
        rules_dir = tempfile.mkdtemp(prefix="baseline_rules_")
        try:
            with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
                 patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
                 patch.object(domain_gate, "_approval_rules_file",
                              os.path.join(rules_dir, "absent.json")):
                yield
        finally:
            shutil.rmtree(rules_dir, ignore_errors=True)


def classification_entries() -> list:
    """按固定样例采集全部分级快照条目（绑定域工具双分支）。"""
    bound_domain_tools = domain_gate_bound_tools()
    entries = []
    for name, args in TOOL_SAMPLE_ARGS.items():
        branches = (BRANCH_BOUND_DOMAIN_REMOVED, BRANCH_DEFAULT) if \
            name in bound_domain_tools else (BRANCH_DEFAULT,)
        for branch in branches:
            with domain_gate_branch(branch):
                assess = classify_tool_call(name, args)
            entries.append({
                "tool": name,
                "args": args,
                "domain_gate": branch,
                "risk_class": assess.risk_class,
                "targets": list(assess.targets),
            })
    for command in SHELL_SAMPLE_COMMANDS:
        assess = classify_tool_call("execute_office_shell", {"command": command})
        entries.append({
            "tool": "execute_office_shell",
            "args": {"command": command},
            "domain_gate": BRANCH_DEFAULT,
            "risk_class": assess.risk_class,
            "targets": list(assess.targets),
        })
    return entries


def domain_gate_bound_tools() -> frozenset:
    """core 名册里的绑定域工具名（条件分级的双分支对象）。"""
    from auditronclaw.core.approval.classifier import _BOUND_DOMAIN_TOOLS
    return frozenset(_BOUND_DOMAIN_TOOLS)


class TestPreRefactorToolSetBaseline(unittest.TestCase):
    """基线一：默认装配工具集合逐项等于改造前（含 shell；技能/外接见夹具注）。"""

    def test_assembled_tool_set_equals_baseline(self):
        with open(TOOL_SET_FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        self.assertEqual(assembled_tool_names(), fixture["tools"],
                         "装配工具集合（含顺序）漂移——语义不变被破坏，或基尺被动过")


class TestPreRefactorClassificationBaseline(unittest.TestCase):
    """基线二：每条 (工具, 样例参数, 域名门分支) 的 (级别, targets) 等于改造前。"""

    def test_classification_snapshot_equals_baseline(self):
        with open(CLASSIFICATION_FIXTURE, encoding="utf-8") as f:
            fixture = json.load(f)
        for entry in fixture["entries"]:
            with self.subTest(tool=entry["tool"], branch=entry["domain_gate"],
                              args=entry["args"]):
                with domain_gate_branch(entry["domain_gate"]):
                    assess = classify_tool_call(entry["tool"], entry["args"])
                self.assertEqual(assess.risk_class, entry["risk_class"])
                self.assertEqual(list(assess.targets), entry["targets"])


def regenerate_fixtures() -> None:
    """把当刻装配与分级事实写成夹具（改造前一次性执行）。"""
    os.makedirs(BASELINE_DIR, exist_ok=True)
    tool_set = {
        "description": "改造前默认装配工具集合（build_builtin_tools 产物，装配顺序）",
        "notes": "默认装配不含技能与外接工具：前者随工作区内容装配（默认为空），"
                 "后者由调用方注入。集合等值检查的覆盖边界与此一致。",
        "tools": assembled_tool_names(),
    }
    with open(TOOL_SET_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(tool_set, f, ensure_ascii=False, indent=2)
        f.write("\n")
    classification = {
        "description": "改造前分级快照：签名 (工具, 固定样例参数, 域名门分支) → (级别, targets)",
        "notes": "技能与外接工具设计上 unclassified（fail-closed 默认必批），不拍分级快照。",
        "entries": classification_entries(),
    }
    with open(CLASSIFICATION_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(classification, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"已重建: {TOOL_SET_FIXTURE}")
    print(f"已重建: {CLASSIFICATION_FIXTURE}")


if __name__ == "__main__":
    regenerate_fixtures()
