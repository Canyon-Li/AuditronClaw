"""域名扩展流程 05 票:白名单扩展副作用类的从无到有。

词汇见 CONTEXT.md「域名白名单」:名单 = 默认名单 ∪ 环境变量扩展 ∪ 运行时
审批规则——经审批门"永久允许"铸入、运行期生效。被拦的是"扩展尝试"
(绑定域在名单外的工具调用,分级 domain_extend),不是运行时 URL——参数面
无 URL 字段是既有原则。

分层(沿用仓库测试纪律):
- 真实组件:RuleStore 铸域名规则 → check_domain_allowed 即时放行(不重启);
  通配域作用域;非域名规则不放行域名;撤销即失效;环境变量守卫
- 引擎级闭环(v5 验收项):无人拦 → 审批"永久允许" → 规则落盘带域名 →
  同域名调用无人静默放行;rule_persisted 事件携带域名
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

from helpers import FakeSender, InjectedSender

from auditronclaw.core.approval.classifier import RISK_DOMAIN_EXTEND
from auditronclaw.core.approval.rules import RuleStore
from auditronclaw.core.tools import domain_gate
from auditronclaw.core.tools.domain_gate import check_domain_allowed

# 强制"绑定域在名单外"的名单形状:保住邮箱域(与被测域无关),挤出飞书域
_NO_FEISHU_DEFAULTS = {"imap.qq.com"}
FEISHU_DOMAIN = "open.feishu.cn"


class DomainRuleTestBase(unittest.TestCase):
    """域名规则测试公共件:临时规则文件 + 名单外基线(extend 路径的前提)。"""

    def setUp(self):
        tmp_dir = tempfile.mkdtemp(prefix="domain_rules_test_")
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        self.rules_path = os.path.join(tmp_dir, "approval_rules.json")
        # domain_gate 的规则读取与门的 matcher 走同一模块属性(生产同源)
        patchers = [
            patch('auditronclaw.core.approval.rules.APPROVAL_RULES_FILE',
                  self.rules_path),
            patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS",
                         set(_NO_FEISHU_DEFAULTS)),
            patch.object(domain_gate, "_EXTENDED_DOMAINS", set()),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _seed(self, entries: list) -> None:
        """预置规则文件(夹具形态:与 06 票预置同路,不经 persist)。"""
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)


# ============ 运行期生效:铸规则 → 守卫即时放行(经 refresh 生产路径) ============

class TestApprovalRuleDomainsRuntime(DomainRuleTestBase):
    """审批规则成为名单第三来源:铸入/撤销/预置当次生效,不重启。"""

    def test_before_rule_domain_denied(self):
        """基线:绑定域被移出默认名单且无规则 → 名单外拒绝(冷启动形态)"""
        self.assertFalse(check_domain_allowed(FEISHU_DOMAIN))
        # 名单内域名不受影响(守卫只对名单外走规则路径)
        self.assertTrue(check_domain_allowed("imap.qq.com"))

    def test_persisted_domain_rule_allows_without_restart(self):
        """铸规则即生效:同进程 persist 后同域名放行,无需重启或手动刷新"""
        self.assertFalse(check_domain_allowed(FEISHU_DOMAIN))
        rule = RuleStore().persist_rule(RISK_DOMAIN_EXTEND, FEISHU_DOMAIN,
                                        "approval", thread_id="domain_test")
        self.assertEqual(rule.action, RISK_DOMAIN_EXTEND)
        self.assertTrue(check_domain_allowed(FEISHU_DOMAIN),
                        "域名规则落盘后,同域名判定必须当次放行")

    def test_seeded_wildcard_scope_matches_subdomains(self):
        """通配域作用域:夹具预置 *.feishu.cn → 子域放行,域外照拒"""
        self._seed([{"id": "w1", "action": "domain_extend", "scope": "*.feishu.cn",
                     "source": "bench_fixture", "created_at": "2026-08-27T00:00:00Z"}])
        self.assertTrue(check_domain_allowed(FEISHU_DOMAIN))
        self.assertFalse(check_domain_allowed("evil.example"),
                         "通配域只放行匹配子域,不是全放行")

    def test_non_domain_rule_does_not_whitelist_domain(self):
        """动作必须相等:write 级规则的作用域再像域名也不放行网络"""
        self._seed([{"id": "w1", "action": "write", "scope": "*.feishu.cn",
                     "source": "approval", "created_at": "2026-08-27T00:00:00Z"}])
        self.assertFalse(check_domain_allowed(FEISHU_DOMAIN),
                         "只有 domain_extend 规则是名单来源,别的动作管不着网络")

    def test_revoked_domain_rule_denies_again(self):
        """撤销即失效:撤销后同域名回到名单外(即时读盘,不重启)"""
        store = RuleStore()
        rule = store.persist_rule(RISK_DOMAIN_EXTEND, FEISHU_DOMAIN, "approval")
        self.assertTrue(check_domain_allowed(FEISHU_DOMAIN))
        store.revoke_rule(rule.id)
        self.assertFalse(check_domain_allowed(FEISHU_DOMAIN))

    def test_env_change_sensed_once_per_change(self):
        """refresh 生产路径同时感知环境变量变化,且 raw 变化才重审计"""
        with patch.object(domain_gate, "audit_logger") as mock_logger, \
             patch.dict(os.environ, {"AUDITRONCLAW_ALLOWED_DOMAINS": "a.example"}):
            self.assertTrue(check_domain_allowed("a.example"))
            self.assertTrue(check_domain_allowed("a.example"))
        env_logs = [c for c in mock_logger.log_event.call_args_list
                    if "AUDITRONCLAW_ALLOWED_DOMAINS" in c.kwargs.get("content", "")]
        self.assertEqual(len(env_logs), 1,
                         "同一次环境变化只留一次扩展审计,不随判定次数翻倍")


# ============ 引擎级闭环(v5 验收项:白名单外域名联网被拦,"永久允许"持久化为规则) ============

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from auditronclaw.core.approval.gate import (
    DecisionSource,
    EVENT_APPROVAL_DECISION,
    EVENT_RULE_PERSISTED,
    REJECT_PHRASE,
    ApprovalDecision,
    TurnOrigin,
)
from auditronclaw.core.session import (
    ApprovalRequest,
    Reply,
    SessionEngine,
    ToolResult,
    TurnEnd,
)
from auditronclaw.core.tools.feishu_tool import send_feishu_summary


class ScriptedLLM:
    """假 LLM:按脚本逐条吐 AIMessage(先例:test_approval_interrupt)。"""

    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, config=None):
        assert self.script, "假 LLM 脚本耗尽"
        return self.script.pop(0)


def _push_call(call_id: str, text: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": "send_feishu_summary", "args": {"summary_text": text},
         "id": call_id, "type": "tool_call"}])


class TestDomainExtensionClosedLoop(DomainRuleTestBase):
    """真工具三回合:无人拦 → 永久允许铸规则落盘 → 同域名无人静默放行。"""

    def test_unattended_rejected_then_persist_then_silent_pass(self):
        fake = FakeSender()
        calls = 0

        def responder(req: ApprovalRequest):
            return ApprovalDecision(True, True, DecisionSource.USER_PERSIST)

        script = []
        for i in (1, 2, 3):
            script.append(_push_call(f"call_{i}", f"第{i}回合日报"))
            script.append(AIMessage(content=f"第{i}回合完成。"))

        with ExitStack() as stack, InjectedSender(fake):
            stack.enter_context(patch(
                'auditronclaw.core.agent.get_provider',
                return_value=ScriptedLLM(script)))
            stack.enter_context(patch('auditronclaw.core.agent.BUILTIN_TOOLS',
                                      [send_feishu_summary]))
            stack.enter_context(patch('auditronclaw.core.agent.load_dynamic_skills',
                                      return_value=[]))
            audit_mock = stack.enter_context(
                patch('auditronclaw.core.approval.gate.audit_logger'))
            stack.enter_context(patch(
                'auditronclaw.core.tools.feishu_tool.audit_logger'))
            stack.enter_context(patch(
                'auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url',
                return_value="https://open.feishu.cn/open-apis/bot/v2/hook/SECRET_TOKEN"))
            from auditronclaw.core.agent import create_agent_app
            app = create_agent_app(provider_name="fake", model_name="fake-model",
                                   checkpointer=MemorySaver(), thread_id="domain_loop")
            engine = SessionEngine(app, "domain_loop", approval_responder=responder)

            def drive(text, origin):
                events = []

                async def run():
                    async for ev in engine.run_turn(text, origin=origin):
                        events.append(ev)

                asyncio.run(run())
                return events

            unattended = drive("【系统内部心跳触发】\n推送日报", TurnOrigin.HEARTBEAT)
            sent_after_unattended = len(fake.sent)
            attended = drive("推送日报", TurnOrigin.HUMAN)
            sent_after_attended = len(fake.sent)
            silent = drive("【系统内部心跳触发】\n推送日报", TurnOrigin.HEARTBEAT)
            sent_after_silent = len(fake.sent)

        # 回合一(心跳):无规则且无人 → 拒绝并继续,传输层零触碰
        self.assertNotIn(ApprovalRequest, [type(e) for e in unattended])
        results = [e for e in unattended if isinstance(e, ToolResult)]
        self.assertEqual(len(results), 1)
        self.assertIn(REJECT_PHRASE, results[0].result)
        self.assertIn(FEISHU_DOMAIN, results[0].result, "拒绝话术点名具体域名")
        self.assertEqual(sent_after_unattended, 0, "被拒的推送不得触达传输层")
        self.assertIsInstance(unattended[-1], TurnEnd)

        # 回合二(人):interrupt 问人,载荷带 domain_extend 级与完整参数
        requests = [e for e in attended if isinstance(e, ApprovalRequest)]
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].risk_class, RISK_DOMAIN_EXTEND)
        self.assertEqual(requests[0].args, {"summary_text": "第2回合日报"})
        self.assertIn(FEISHU_DOMAIN, requests[0].reason)
        # 永久允许:先铸规则后执行——本次推送即真实发出(内层域名门同走新规则)
        self.assertEqual(sent_after_attended, 1, "批准且已铸规则的推送必须真实发出")

        # 回合三(心跳):域名经规则入名单 → 分级免批 → 无人静默放行
        self.assertNotIn(ApprovalRequest, [type(e) for e in silent])
        results = [e for e in silent if isinstance(e, ToolResult)]
        self.assertEqual(len(results), 1)
        self.assertNotIn(REJECT_PHRASE, results[0].result)
        self.assertEqual(sent_after_silent, 2, "规则生效后的同域名调用照常推送")
        self.assertIsInstance(silent[-1], TurnEnd)

        # 规则落盘:恰好一条域名规则,动作与作用域就是本次批准的那份
        with open(self.rules_path, encoding="utf-8") as f:
            entries = json.load(f)
        self.assertEqual(entries, [{
            "id": entries[0]["id"], "action": "domain_extend",
            "scope": FEISHU_DOMAIN, "source": "approval",
            "created_at": entries[0]["created_at"],
        }])

        # 审计:无人拒 → 人批持久 → rule_persisted 携带域名;回合三零审批事件
        decisions = [c.kwargs for c in audit_mock.log_event.call_args_list
                     if c.kwargs.get("event") == EVENT_APPROVAL_DECISION]
        self.assertEqual([d["source"] for d in decisions],
                         [DecisionSource.UNATTENDED.value,
                          DecisionSource.USER_PERSIST.value])
        persisted = [c.kwargs for c in audit_mock.log_event.call_args_list
                     if c.kwargs.get("event") == EVENT_RULE_PERSISTED]
        self.assertEqual(len(persisted), 1)
        self.assertEqual(persisted[0]["rule"]["scope"], FEISHU_DOMAIN,
                         "rule_persisted 事件必须携带域名")


if __name__ == '__main__':
    unittest.main()
