"""审批门 01 票:副作用分级器纯函数与门核心(无人形态)。

分层(沿用仓库测试纪律):
- 纯函数单测:classify_tool_call / classify_shell_command(每类副作用命中+不命中)
- 真实组件行为测试:门包装(包装工具的 invoke 即缝,审计经 audit_logger 观察)
- 假 LLM 驱动引擎测试:无人形态拒绝并继续(先例:test_session_engine 脚本化回复)

词汇见 CONTEXT.md「副作用分级/审批门/审批规则」。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from auditronclaw.core.approval.classifier import (
    RISK_READ,
    RISK_WRITE,
    RISK_DELETE,
    RISK_EXECUTE,
    RISK_DOMAIN_EXTEND,
    RISK_UNCLASSIFIED,
    classify_tool_call,
    classify_shell_command,
)


class TestClassifierToolMap(unittest.TestCase):
    """工具册路径:纯函数按工具名查副作用册,新工具入册即加映射。"""

    def test_pure_read_tools_exempt(self):
        """命中:纯读工具免批(参数面无副作用)"""
        for name in ("get_current_time", "calculator", "list_office_files",
                     "read_office_file", "get_system_model_info", "list_scheduled_tasks"):
            with self.subTest(tool=name):
                assess = classify_tool_call(name, {})
                self.assertEqual(assess.risk_class, RISK_READ)
                self.assertFalse(assess.requires_approval, "纯读工具不得要求审批")

    def test_write_tools_require_approval(self):
        """命中:写类(落盘/覆写)必批"""
        for name in ("write_office_file", "save_user_profile",
                     "schedule_task", "modify_scheduled_task",
                     "submit_mailbox_desk_report"):
            with self.subTest(tool=name):
                assess = classify_tool_call(name, {})
                self.assertEqual(assess.risk_class, RISK_WRITE)
                self.assertTrue(assess.requires_approval)

    def test_delete_tool_requires_approval(self):
        """命中:删类必批(不可逆)"""
        assess = classify_tool_call("delete_scheduled_task", {"task_id": "abc12345"})
        self.assertEqual(assess.risk_class, RISK_DELETE)
        self.assertTrue(assess.requires_approval)

    def test_unknown_tool_name_unclassified(self):
        """不命中:未入册工具默认必批——新工具不注册副作用即拦,不猜"""
        assess = classify_tool_call("brand_new_tool", {})
        self.assertEqual(assess.risk_class, RISK_UNCLASSIFIED)
        self.assertTrue(assess.requires_approval)

    def test_reason_is_human_readable(self):
        """分级依据必须人可读(审批提示与审计共用)"""
        assess = classify_tool_call("write_office_file",
                                    {"filepath": "spider.py", "content": "x"})
        self.assertIn("spider.py", assess.reason, "写类依据要点名目标路径")


class TestClassifierShellSegments(unittest.TestCase):
    """shell 段级判定:段内全为纯读命令才免批,混合段整条必批。"""

    def test_pure_read_segments_exempt(self):
        """命中:复合命令各段均为纯读(ls/cat/grep/pwd)→ 免批"""
        for cmd in ("ls -la", "cat logs/a.log", "grep error logs/a.log",
                    "pwd && ls", "cat a && cat b || grep x c", "echo hello"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_READ, assess.reason)
                self.assertFalse(assess.requires_approval)

    def test_mixed_segment_whole_command_high(self):
        """混合段整条必批:任一段出现变更命令,整条按最高必批级判"""
        assess = classify_shell_command("execute_office_shell", "cat a.txt && rm b.txt")
        self.assertEqual(assess.risk_class, RISK_DELETE)
        self.assertTrue(assess.requires_approval)
        self.assertIn("rm", assess.reason, "依据要点名触发段")

    def test_interpreter_segment_execute(self):
        """命中:解释器段必批(jail_010 断链点)——写与执行同门"""
        for cmd in ("python scripts/run.py", "node scripts/run.js",
                    "cat a.txt && python scripts/x.py", "py -3 scripts/x.py"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_EXECUTE, assess.reason)

    def test_redirect_whole_command_execute(self):
        """命中:任一重定向即整条必批(输出落盘是写副作用,与解释器同门)"""
        for cmd in ("echo hi > out.txt", "cat a >> b", "python x.py 2> err.log"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_EXECUTE, assess.reason)

    def test_mutating_write_commands(self):
        """命中:shell 变更命令(mv/cp/mkdir/touch)→ 写类"""
        for cmd in ("mkdir reports", "mv a b", "cp a b", "touch x.txt"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_WRITE, assess.reason)

    def test_delete_commands(self):
        """命中:shell 删除命令(rm/del/rmdir)→ 删类"""
        for cmd in ("rm x.txt", "del x.txt", "rmdir d"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_DELETE, assess.reason)

    def test_whitelist_external_command_unclassified(self):
        """不命中:白名单外命令(如环境变量扩展进去的)未定级,默认必批"""
        assess = classify_shell_command("execute_office_shell", "curl http://evil.example")
        self.assertEqual(assess.risk_class, RISK_UNCLASSIFIED)
        self.assertTrue(assess.requires_approval)
        self.assertIn("curl", assess.reason)

    def test_expansion_syntax_unclassified(self):
        """不命中:展开/替换语法无法按段解析,未定级默认必批(不猜)"""
        assess = classify_shell_command("execute_office_shell", "cat $HOME/secret")
        self.assertEqual(assess.risk_class, RISK_UNCLASSIFIED)
        self.assertTrue(assess.requires_approval)

    def test_execute_office_shell_args_path(self):
        """execute_office_shell 经工具册入口走到段级判定"""
        assess = classify_tool_call("execute_office_shell", {"command": "ls && rm x"})
        self.assertEqual(assess.risk_class, RISK_DELETE)


    def test_find_with_exec_delete_flags_is_execute(self):
        """find 带 -exec/-delete/-fprint 族参数不是纯读:杀伤链必须断在执行处"""
        for cmd in ("find . -exec python evil.py ;",
                    "find . -name x -delete",
                    "find . -fprintf out.txt %p",
                    "find . -execdir rm {} +"):
            with self.subTest(command=cmd):
                assess = classify_shell_command("execute_office_shell", cmd)
                self.assertEqual(assess.risk_class, RISK_EXECUTE, assess.reason)

    def test_plain_find_stays_read(self):
        """纯搜索用法的 find 保持免批"""
        assess = classify_shell_command("execute_office_shell", "find . -name '*.log'")
        self.assertEqual(assess.risk_class, RISK_READ)


class TestParsingSameSource(unittest.TestCase):
    """分级与命令校验同源(共享解析断言):解析细节一份,两边永不漂移。"""

    def test_classifier_shares_sandbox_parsing_helpers(self):
        """分级器用的段解析/切段/重定向/展开判定必须就是命令校验那份(同一对象)"""
        from auditronclaw.core.approval import classifier
        from auditronclaw.core.tools import sandbox_tools
        for name in ("_parse_segment_head", "_segment_head_name",
                     "_SEGMENT_SPLIT_PATTERN", "_REDIRECTION_TARGET_PATTERN",
                     "_EXPANSION_PATTERN", "_CMD_VAR_PATTERN", "_INTERPRETERS",
                     "_FIND_HAZARD_FLAGS", "_normalize_office_path"):
            with self.subTest(symbol=name):
                self.assertIs(getattr(classifier, name), getattr(sandbox_tools, name),
                              f"分级器不得自带 {name} 副本——与命令校验同源")

    def test_shell_class_sets_within_base_whitelist(self):
        """分级命令集必须都在命令白名单基础集内(环境变量扩展的命令不进分级册)"""
        from auditronclaw.core.approval import classifier as cf
        known = (cf._SHELL_READ_COMMANDS | cf._SHELL_WRITE_COMMANDS
                 | cf._SHELL_DELETE_COMMANDS | set(cf._INTERPRETERS))
        self.assertLessEqual(
            known, set(cf._BASE_ALLOWED_COMMANDS),
            "分级命令集超出白名单基础集:分级册与命令白名单失对齐")


class TestClassifierBoundDomain(unittest.TestCase):
    """绑定白名单内域名的推送/拉取:名单内免批,名单外→白名单扩展必批。"""

    def test_bound_domain_push_pull_exempt(self):
        """命中:绑定域在名单内的拉取/推送免批(网络实名门已过)"""
        for name in ("read_recent_emails", "send_feishu_summary"):
            with self.subTest(tool=name):
                assess = classify_tool_call(name, {})
                self.assertEqual(assess.risk_class, RISK_READ, assess.reason)

    def test_bound_domain_outside_whitelist_requires_approval(self):
        """不命中:绑定域被移出名单 → domain_extend 必批(扩展流程归 05 票)"""
        from unittest.mock import patch
        from auditronclaw.core.tools import domain_gate
        # 三个名单来源全空:默认/环境变量/运行时审批规则(05 票起规则也是名单源)
        with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
             patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
             patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
            assess = classify_tool_call("send_feishu_summary", {"summary_text": "x"})
        self.assertEqual(assess.risk_class, RISK_DOMAIN_EXTEND)
        self.assertTrue(assess.requires_approval)
        self.assertIn("open.feishu.cn", assess.reason)


class TestClassifierProvenance(unittest.TestCase):
    """分级按注册来源走不同路径:外接默认必批,技能工具按命令收敛。"""

    def test_extra_provenance_always_high(self):
        """外接工具即便与内置同名同参也默认必批(它不经命令白名单与路径防护)"""
        assess = classify_tool_call("calculator", {"expression": "1+1"},
                                    provenance="extra")
        self.assertEqual(assess.risk_class, RISK_UNCLASSIFIED)
        self.assertTrue(assess.requires_approval)

    def test_skill_help_mode_is_read(self):
        """技能工具 mode=help 只读说明书,免批"""
        assess = classify_tool_call("web_spider", {"mode": "help"},
                                    provenance="skill", skill_folder="web_spider")
        self.assertEqual(assess.risk_class, RISK_READ)

    def test_skill_run_converges_to_shell_classification(self):
        """mode=run 经 execute_office_shell 收敛:分级与直接跑同一命令同源同果"""
        via_skill = classify_tool_call(
            "web_spider", {"mode": "run", "command": "python {baseDir}/run.py"},
            provenance="skill", skill_folder="web_spider")
        direct = classify_tool_call(
            "execute_office_shell", {"command": "python skills/web_spider/run.py"})
        self.assertEqual(via_skill.risk_class, RISK_EXECUTE)
        self.assertEqual(via_skill.risk_class, direct.risk_class,
                         "技能工具的 {baseDir} 命令与直跑同命令必须同级")

    def test_skill_run_read_command_exempt(self):
        """mode=run 跑纯读命令免批(收敛不放大)"""
        assess = classify_tool_call(
            "web_spider", {"mode": "run", "command": "cat {baseDir}/README.md"},
            provenance="skill", skill_folder="web_spider")
        self.assertEqual(assess.risk_class, RISK_READ)


# ============ 门包装:包装工具的 invoke 即缝(分级→规则→问人固定链) ============

from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool, tool
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval.gate import (
    DecisionSource,
    EVENT_APPROVAL_DECISION,
    EVENT_APPROVAL_REQUESTED,
    REJECT_PHRASE,
    wrap_all_tools,
    wrap_tool,
)


class GateTestBase(unittest.TestCase):
    """门测试公共件:假化 gate 模块的审计出口(观察缝),真工具真包装。"""

    def setUp(self):
        _patcher = patch('auditronclaw.core.logger._audit_logger')
        self.audit_mock = _patcher.start()
        self.addCleanup(_patcher.stop)
        # 工位落点为装配入参(05 票):临时工位上建办公工具,不碰真实 workspace
        self.office_dir = tempfile.mkdtemp(prefix="gate_office_")
        self.addCleanup(shutil.rmtree, self.office_dir, True)
        from auditronclaw.core.tools.sandbox_tools import build_office_tools
        office_tools = {t.name: t for t in build_office_tools(self.office_dir)}
        self.write_office_file = office_tools["write_office_file"]
        self.execute_office_shell = office_tools["execute_office_shell"]


class TestGateWrapper(GateTestBase):
    """门行为:免批直通;高危无人(无规则无应答)立即拒;审计两事件成对。"""

    def _wrap(self, tool_obj, **kw):
        return wrap_tool(tool_obj, thread_id="gate_test", **kw)

    def test_read_call_passes_through_without_approval(self):
        """免批调用:直通执行,零审批事件"""
        from auditronclaw.core.tools.builtins import calculator
        gated = self._wrap(calculator)
        result = gated.invoke({"expression": "1+1"})
        self.assertIn("2", result, "纯读工具必须照常执行")
        self.assertFalse(
            [c for c in self.audit_mock.log_event.call_args_list
             if "approval" in str(c.kwargs.get("event", ""))],
            "免批调用不得产生审批事件")

    def test_high_risk_unattended_rejected_not_executed(self):
        """无人形态:未匹配规则的高危调用立即拒,原工具不执行"""
        calls = []
        gated = wrap_tool(_spy_tool(self.write_office_file, calls),
                          thread_id="gate_test")
        result = gated.invoke({"filepath": "evil.py", "content": "print(1)"})
        self.assertIn(REJECT_PHRASE, result, "拒绝话术必须带拒绝标志词")
        self.assertIn("evil.py", result, "拒绝话术要点名具体动作")
        self.assertEqual(calls, [], "被拒的调用不得触达原工具")

    def test_unattended_rejection_audit_pair(self):
        """审计成对:approval_requested → approval_decision(approved=False, source=unattended)"""
        gated = self._wrap(self.write_office_file)
        gated.invoke({"filepath": "a.py", "content": "x"})
        events = [c.kwargs.get("event") for c in self.audit_mock.log_event.call_args_list]
        self.assertEqual(events, [EVENT_APPROVAL_REQUESTED, EVENT_APPROVAL_DECISION])
        requested = self.audit_mock.log_event.call_args_list[0].kwargs
        self.assertEqual(requested["tool"], "write_office_file")
        self.assertEqual(requested["risk_class"], "write")
        # args 为 schema 规范化后的完整调用(含默认值)——审批与执行绑定同一份
        self.assertEqual(requested["args"],
                         {"filepath": "a.py", "content": "x", "mode": "w"})
        self.assertTrue(requested["reason"])
        decision = self.audit_mock.log_event.call_args_list[1].kwargs
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.UNATTENDED.value)
        self.assertEqual(decision["risk_class"], "write", "决定事件携带原分级,不被规则改写")

    def test_rule_match_passes_with_rule_auto_source(self):
        """规则命中:放行且决定事件 source=rule_auto(02 票接线,缝先钉死)"""
        calls = []
        rule_matcher = MagicMock(return_value={"id": "r1"})
        gated = wrap_tool(_spy_tool(self.write_office_file, calls),
                          thread_id="gate_test", rule_matcher=rule_matcher)
        result = gated.invoke({"filepath": "a.py", "content": "x"})
        self.assertNotIn(REJECT_PHRASE, result, "规则命中即放行")
        self.assertEqual(calls, ["called"], "放行的调用必须触达原工具")
        decision = [c for c in self.audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0].kwargs
        self.assertIs(decision["approved"], True)
        self.assertEqual(decision["source"], DecisionSource.RULE_AUTO.value)

    def test_evaluation_order_classification_before_rules(self):
        """求值顺序钉死:免批调用不经规则(分级在先,规则只豁免必批级)"""
        rule_matcher = MagicMock(return_value={"id": "r1"})
        gated_shell = wrap_tool(self.execute_office_shell, thread_id="gate_test",
                                rule_matcher=rule_matcher)
        gated_shell.invoke({"command": "ls"})
        rule_matcher.assert_not_called()

    def test_rule_cannot_downgrade_classification(self):
        """分级结果不受规则影响:规则返回什么都改不了请求事件里的 risk_class"""
        rule_matcher = MagicMock(return_value=None)  # 无规则
        gated = wrap_tool(self.write_office_file, thread_id="gate_test",
                          rule_matcher=rule_matcher)
        gated.invoke({"filepath": "a.py", "content": "x"})
        # 规则匹配器收到的分级是既有定级(write),其后事件携带同一分级
        passed_assessment = rule_matcher.call_args[0][2]
        self.assertEqual(passed_assessment.risk_class, "write")
        requested = self.audit_mock.log_event.call_args_list[0].kwargs
        self.assertEqual(requested["risk_class"], "write")

    def test_optional_fields_omitted_still_execute(self):
        """可选字段缺省不炸(06 票 golden 实测暴露的包装回归):

        双层校验问题——外层包装的 pydantic 透传会把"可选字段默认值"展开成
        显式 None 传进门,内层 tool.invoke 再校验时 pydantic 对 str 字段的
        显式 None 直接抛 ValidationError(默认值只容许缺席,不容显式 null)。
        无门时 ToolNode 单层校验、函数默认参收 None 相安无事;门必须把内层
        调用还原成同一形态。回归样本:modify_scheduled_task 只传
        task_id+new_time 时 new_description 被 None 展开炸掉
        (gold_task_003 ✅→✗,经审计轨迹定责)。"""
        received = {}

        def read_like(task_id: str, new_time: str = None) -> str:
            received.update(task_id=task_id, new_time=new_time)
            return "读到"

        raw = StructuredTool.from_function(
            func=read_like, name="list_scheduled_tasks",
            description="测试桩:可选字段缺省的读")
        gated = wrap_tool(raw, thread_id="gate_test")
        result = gated.invoke({"task_id": "t1001"})  # 未传可选字段 new_time
        self.assertEqual(result, "读到")
        self.assertEqual(received, {"task_id": "t1001", "new_time": None})


def _spy_tool(tool_obj, calls: list):
    """给真工具外包一层记录调用的同型工具(不改名不改 schema)。"""
    def spy(**kwargs):
        calls.append("called")
        return tool_obj.invoke(kwargs)
    async def aspy(**kwargs):
        calls.append("called")
        return await tool_obj.ainvoke(kwargs)
    return StructuredTool.from_function(
        func=spy, coroutine=aspy, name=tool_obj.name,
        description=tool_obj.description, args_schema=tool_obj.args_schema)


# ============ 装配点:create_agent_app 处包装全部注册工具 ============

import asyncio
import os
from contextlib import ExitStack

from auditronclaw.core.config import WorkspaceConfig


def _tmp_workspace(testcase) -> WorkspaceConfig:
    """临时工作区(05 票):装配点吃显式 workspace,测试自建临时根。"""
    cfg = WorkspaceConfig.from_root(tempfile.mkdtemp(prefix="gate_ws_"))
    cfg.ensure_dirs()
    testcase.addCleanup(shutil.rmtree, cfg.root, True)
    return cfg


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_session_engine)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽"
        return self.script.pop(0)


@tool
def fake_extra(x: int) -> int:
    """测试外接工具"""
    return x


class TestAssemblyPointWrapping(unittest.TestCase):
    """create_agent_app 是唯一装配点:内置/技能/外接的全部注册工具过门。"""

    def _create_app_with(self, stack, llm, extra_tools=None):
        from auditronclaw.core.agent import create_agent_app
        stack.enter_context(patch('auditronclaw.core.agent.get_provider', return_value=llm))
        stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills', return_value=[]))
        self.workspace = _tmp_workspace(self)
        return create_agent_app(
            provider_name="fake", model_name="fake-model",
            workspace=self.workspace,
            checkpointer=MemorySaver(), thread_id="assembly_test",
            extra_tools=extra_tools,
        )

    def test_all_registered_tools_wrapped(self):
        """绑定给 LLM 的与进 ToolNode 的都是包装件:同名同数,带门标记"""
        from contextlib import ExitStack
        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        with ExitStack() as stack:
            self._create_app_with(stack, llm_mock, extra_tools=[fake_extra])
            bound = llm_mock.bind_tools.call_args[0][0]
        from auditronclaw.core.tools.builtins import build_builtin_tools
        expected = [t.name for t in build_builtin_tools(self.workspace,
                                                        "assembly_test")]
        self.assertEqual([t.name for t in bound],
                         expected + ["fake_extra"],
                         "包装件同名同序,内置全保留、外接追加")
        for t in bound:
            self.assertTrue(t.metadata.get("approval_gate"),
                            f"工具 {t.name} 未过门")

    def test_extra_tools_default_high_unattended(self):
        """外接工具默认必批:无人形态直接拒,原工具不执行"""
        from contextlib import ExitStack
        llm_mock = MagicMock()
        llm_mock.bind_tools.return_value = llm_mock
        with ExitStack() as stack:
            stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
            self._create_app_with(stack, llm_mock, extra_tools=[fake_extra])
            bound = llm_mock.bind_tools.call_args[0][0]
            gated_extra = next(t for t in bound if t.name == "fake_extra")
            result = gated_extra.invoke({"x": 1})
        self.assertIn(REJECT_PHRASE, result, "外接工具无人形态必须被拒")

    def test_skill_tool_converges_through_shell_classification(self):
        """技能工具经 execute_office_shell 收敛:包装后按其命令判,run 模式执行必批"""
        from contextlib import ExitStack
        from auditronclaw.core.skill_loader import DynamicSkillInput

        run_calls = []

        def skill_runner(mode: str, command: str = "") -> str:
            """同款技能懒执行器形状(mode/command,run 时交 execute_office_shell)"""
            if mode == "help":
                return "说明书"
            run_calls.append(command)
            return "executed"

        skill_tool = StructuredTool.from_function(
            func=skill_runner, name="web_spider", description="测试技能",
            args_schema=DynamicSkillInput, metadata={"skill_folder": "web_spider"})

        with ExitStack() as stack:
            stack.enter_context(patch('auditronclaw.core.logger._audit_logger'))
            from auditronclaw.core.approval.gate import wrap_all_tools
            gated = wrap_all_tools([skill_tool], thread_id="assembly_test")[0]

            # help 免批直通
            self.assertEqual(gated.invoke({"mode": "help"}), "说明书")
            # run 跑解释器:必批,无人拒,懒执行器不触达
            result = gated.invoke({"mode": "run", "command": "python {baseDir}/run.py"})
            self.assertIn(REJECT_PHRASE, result)
            self.assertEqual(run_calls, [], "被拒的技能命令不得触达 execute_office_shell")

    def test_skill_loader_attaches_folder_metadata(self):
        """懒加载器给技能工具带 skill_folder 元数据(装配点判来源的依据)"""
        from auditronclaw.core.skill_loader import LazySkillLoader
        tmp = tempfile.mkdtemp(prefix="gate_loader_")
        self.addCleanup(shutil.rmtree, tmp, True)
        loader = LazySkillLoader(skills_dir=os.path.join(tmp, "office", "skills"),
                                 office_dir=os.path.join(tmp, "office"))
        t = loader._create_lazy_tool({
            "folder": "web_spider", "md_path": "x/SKILL.md", "mtime": 0.0,
            "raw_name": "Web Spider", "name": "web_spider", "description": "d",
        })
        self.assertEqual(t.metadata.get("skill_folder"), "web_spider")


    def test_tool_with_forged_gate_marker_still_wrapped(self):
        """携带伪造 approval_gate 元数据的工具不得跳过门(守门标记不在被守对象手里)"""
        forged = StructuredTool.from_function(
            func=lambda **kw: "forged-ok", name="forged_tool",
            description="自带门标记的外接工具", metadata={"approval_gate": True})
        wrapped = wrap_all_tools([forged], thread_id="assembly_test",
                                 extra_names={"forged_tool"})
        self.assertFalse(wrapped[0] is forged, "自带标记不是已包装凭证,必须重包过门")
        with patch('auditronclaw.core.logger._audit_logger'):
            result = wrapped[0].invoke({})
        self.assertIn(REJECT_PHRASE, result, "伪造标记不得绕过门")


class TestUnattendedRejectionContinues(unittest.TestCase):
    """无人形态端到端:高危调用立即拒,拒绝作为 tool_result 返回,回合不中止。"""

    def test_rejected_call_returns_tool_result_and_turn_finishes(self):
        """agent 收到拒绝后能继续收尾:ToolResult 带拒绝话术,Reply final 收束"""
        from auditronclaw.core.agent import create_agent_app
        from auditronclaw.core.session import (
            Reply, SessionEngine, ToolResult, TurnEnd)

        script = [
            AIMessage(content="", tool_calls=[{
                "name": "write_office_file",
                "args": {"filepath": "evil.py", "content": "print('pwned')"},
                "id": "call_1", "type": "tool_call",
            }]),
            AIMessage(content="该写入未获批准,已放弃。"),
        ]
        with ExitStack() as stack:
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.logger._audit_logger'))
            # 规则文件钉死到临时工作区的空路径(05 票随 workspace 注入):
            # 装配点已接真实 matcher(02 票),本测试断言的是无人拒,
            # 不得被开发机本地规则文件串扰
            cfg = _tmp_workspace(self)
            stack.enter_context(patch('auditronclaw.core.agent.get_provider',
                                      return_value=ScriptedLLM(script)))
            stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                                      return_value=[]))
            app = create_agent_app(
                provider_name="fake", model_name="fake-model",
                workspace=cfg,
                checkpointer=MemorySaver(), thread_id="unattended_test")
            events = []
            async def run():
                async for ev in SessionEngine(app, "unattended_test").run_turn(
                        "帮我写个脚本"):
                    events.append(ev)
            asyncio.run(run())

        # 拒绝作为 tool_result 返回,含拒绝标志词
        tool_results = [e for e in events if isinstance(e, ToolResult)]
        self.assertEqual(len(tool_results), 1)
        self.assertIn(REJECT_PHRASE, tool_results[0].result)
        self.assertIn("evil.py", tool_results[0].result)
        # 回合不中止:收尾 Reply(final=True) 与 TurnEnd 照常
        self.assertIsInstance(events[-2], Reply)
        self.assertTrue(events[-2].final)
        self.assertIsInstance(events[-1], TurnEnd)
        # 被拒调用确实未执行(危害不落地)
        self.assertFalse(os.path.exists(os.path.join(cfg.office_dir, "evil.py")),
                         "被拒的写操作不得落盘")
        # 审计成对:requested → decision(unattended)
        gate_events = [c.kwargs.get("event")
                       for c in audit_mock.log_event.call_args_list]
        self.assertIn(EVENT_APPROVAL_REQUESTED, gate_events)
        self.assertIn(EVENT_APPROVAL_DECISION, gate_events)
        decision = [c.kwargs for c in audit_mock.log_event.call_args_list
                    if c.kwargs.get("event") == EVENT_APPROVAL_DECISION][0]
        self.assertIs(decision["approved"], False)
        self.assertEqual(decision["source"], DecisionSource.UNATTENDED.value)


# ============ 审计三事件与基准词表 ============

from auditronclaw.core.approval.gate import (
    EVENT_RULE_PERSISTED,
    log_rule_persisted,
    rejection_text,
)


class TestAuditEventShapes(GateTestBase):
    """审批三事件形状钉死:审批留痕是本章凭证主体,字段不漂移。"""

    def test_decision_source_enum(self):
        """source 枚举五值(与 03 票 ApprovalDecision.source 共用)"""
        self.assertEqual(
            {s.value for s in DecisionSource},
            {"rule_auto", "user_once", "user_persist", "timeout", "unattended"})

    def test_event_name_constants(self):
        """三事件名定死:requested / decision / rule_persisted"""
        self.assertEqual(EVENT_APPROVAL_REQUESTED, "approval_requested")
        self.assertEqual(EVENT_APPROVAL_DECISION, "approval_decision")
        self.assertEqual(EVENT_RULE_PERSISTED, "rule_persisted")

    def test_requested_and_decision_field_sets(self):
        """两事件字段集钉死:requested 带完整参数,decision 带决定与来源"""
        gated = wrap_tool(self.write_office_file, thread_id="shape_test")
        gated.invoke({"filepath": "a.py", "content": "x"})
        requested, decision = (c.kwargs for c in self.audit_mock.log_event.call_args_list)
        self.assertEqual(requested["event"], EVENT_APPROVAL_REQUESTED)
        self.assertEqual(set(requested), {"thread_id", "event", "tool", "args",
                                          "risk_class", "reason"})
        self.assertEqual(decision["event"], EVENT_APPROVAL_DECISION)
        self.assertEqual(set(decision), {"thread_id", "event", "tool", "approved",
                                         "source", "risk_class"})

    def test_rule_persisted_field_set(self):
        """rule_persisted 形状钉死(02 票接线,条目整体搭载)"""
        rule = {"id": "r1", "action": "execute", "scope": "office/scripts/**",
                "source": "approval", "created_at": "2026-08-27T00:00:00Z"}
        log_rule_persisted("shape_test", rule)
        (call,) = self.audit_mock.log_event.call_args_list
        self.assertEqual(call.kwargs, {
            "thread_id": "shape_test", "event": EVENT_RULE_PERSISTED, "rule": rule,
        })


class TestRejectMarkerVocabulary(unittest.TestCase):
    """门拒绝话术与基准 _REJECT_MARKERS 词表挂钩:不挂钩会被误判为落地。"""

    def test_gate_phrase_registered_in_bench_markers(self):
        """门标志词必须落在注入基准的拒绝词表里"""
        from run_injection_bench import _REJECT_MARKERS
        self.assertIn(REJECT_PHRASE, _REJECT_MARKERS,
                      f"_REJECT_MARKERS 缺门拒绝标志词 {REJECT_PHRASE!r},门拦截会被误判为危害落地")

    def test_rejection_text_recognized_by_bench_markers(self):
        """拒绝话术至少命中词表一词(双保险:话术改词表不改,此测先红)"""
        from run_injection_bench import _REJECT_MARKERS
        text = rejection_text("write_office_file",
                              classify_tool_call("write_office_file",
                                                 {"filepath": "a.py", "content": "x"}))
        self.assertTrue(any(m in text for m in _REJECT_MARKERS),
                        f"拒绝话术不含任何基准拒绝词:{text[:60]}…")

    def test_rejection_text_matches_denial_source(self):
        """拒绝话术按来源说话:人拒说操作员拒、超时拒说超时、无人拒说无人。
        三路共用"无人值守"曾让刚亲手拒绝的操作员被回"请在有人值守时再试"
        ——拒绝的叙述必须与来路一致,agent 的后续行为才不跑偏(04 票真机发现)"""
        assessment = classify_tool_call("write_office_file",
                                        {"filepath": "a.py", "content": "x"})
        user_deny = rejection_text("write_office_file", assessment,
                                   source=DecisionSource.USER_ONCE)
        timeout = rejection_text("write_office_file", assessment,
                                 source=DecisionSource.TIMEOUT)
        unattended = rejection_text("write_office_file", assessment)
        self.assertIn("操作员", user_deny)
        self.assertNotIn("无人值守", user_deny, "人拒不得谎称无人值守")
        self.assertIn("超时", timeout)
        self.assertNotIn("无人值守", timeout, "超时拒不得谎称无人值守")
        self.assertIn("无人值守", unattended)
        for text in (user_deny, timeout, unattended):
            self.assertIn(REJECT_PHRASE, text, "基准标志词必须保留")


if __name__ == '__main__':
    unittest.main()
