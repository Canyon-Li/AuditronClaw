import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from helpers import FakeSender, InjectedSender

from auditronclaw.core.tools import feishu_tool
from auditronclaw.core.tools.desk_tool import create_desk_submit_tool


# ============ 事务台结构化提交工具(function calling 方向定案)============
#
# 弱模型真实运行结论:格式、顺序、副作用靠自然语言指令约束不住(抄速记当标题、
# 跳步、谎报完成)。本工具把控制面移进 function calling:模型只填 schema 字段
# (分类判断),渲染「分类账」、落待办、推送全部代码化,顺序写死——先落盘后
# 推送,"推送失败不吞待办"从提示词变成代码顺序。管线降为 2 次工具调用。


def _report_args(**overrides):
    """一份典型的事务台分类结果(账单→通知携带待办 + 朋友来信→需回复)。"""
    args = {
        "window_hours": 24,
        "total_mails": 3,
        "todos": [
            {"item": "还信用卡账单 ¥2,186.00", "source": "招商银行账单提醒",
             "deadline": (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")},
        ],
        "needs_reply": [
            {"sender": "老王 <laowang@friends.example>", "subject": "周六火锅走不走?"},
        ],
        "notices": [
            {"sender": "招商银行 <ccsvc@example.com>", "subject": "【账单提醒】您本期信用卡账单已出"},
        ],
        "ignorable_count": 1,
        "ignorable_top_senders": ["瑞幸咖啡"],
    }
    args.update(overrides)
    return args


class DeskToolTestCase(unittest.TestCase):
    """公共底座:临时队列落点经工厂注入 + 占位 webhook(真实值永不进断言输出)。"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        self.temp_path = path
        self.submit_tool = create_desk_submit_tool(path)
        self.secret_url = (
            f"https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN_{uuid.uuid4().hex[:12]}"
        )

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.unlink(self.temp_path)

    def _submit(self, sender, args=None):
        """以注入 sender + 占位 webhook 跑一次提交,返回(回执, sender)。"""
        with InjectedSender(sender), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=self.secret_url,
        ):
            result = self.submit_tool.invoke(args or _report_args())
        return result, sender

    def _tasks_on_disk(self):
        if not os.path.exists(self.temp_path):
            return []
        with open(self.temp_path, encoding="utf-8") as f:
            content = f.read().strip()
        return json.loads(content) if content else []


class TestSubmitDeskReport(DeskToolTestCase):
    """提交工具行为:渲染确定性、落盘顺序、失败语义。"""

    def test_renders_ledger_and_lands_todos(self):
        """正常路径:分类账由代码渲染(计头+四段字面标题),待办先落盘"""
        sender = FakeSender()
        result, sender = self._submit(sender)

        # 推送内容:确定性渲染,模型碰不到格式
        self.assertEqual(len(sender.sent), 1)
        text = sender.sent[0][1]["content"]["text"]
        self.assertIn("邮箱事务台日报 | 窗口24小时 | 共3封 · 跨类别待办 1 项", text)
        self.assertIn("■ 待办", text)
        self.assertIn("还信用卡账单 ¥2,186.00 | 招商银行账单提醒", text)
        self.assertIn("■ 需回复", text)
        self.assertIn("周六火锅走不走?", text)
        self.assertIn("■ 通知", text)
        self.assertIn("【账单提醒】", text)
        self.assertIn("■ 可忽略", text)
        self.assertIn("共1封", text)

        # 待办落盘:截止日日期 + 09:00,target 由代码算,不信任模型记时间
        tasks = self._tasks_on_disk()
        self.assertEqual(len(tasks), 1)
        expected_day = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        self.assertEqual(tasks[0]["target_time"], f"{expected_day} 09:00:00")
        self.assertIn("还信用卡账单", tasks[0]["description"])

        # 回执:结构化、脱敏
        self.assertIn("1 项已落任务列表", result)
        self.assertIn("推送成功", result)
        self.assertNotIn("TOKEN_", result)

    def test_expired_or_missing_deadline_lands_tomorrow_0900(self):
        """截止日过期/缺失 → 代码判为明天 09:00(不再指望模型自己算对时间)"""
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        args = _report_args(todos=[
            {"item": "过期待办", "source": "旧账单", "deadline": yesterday},
            {"item": "无截止待办", "source": "口头约定"},
        ])
        result, _ = self._submit(FakeSender(), args)

        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        tasks = self._tasks_on_disk()
        self.assertEqual(len(tasks), 2)
        for t in tasks:
            self.assertEqual(t["target_time"], f"{tomorrow} 09:00:00")
        self.assertIn("2 项已落任务列表", result)

    def test_push_failure_still_lands_todos(self):
        """推送断网:待办已落盘(顺序保障),回执如实报告推送失败"""
        result, _ = self._submit(FakeSender(error=URLError("cannot reach url")))

        tasks = self._tasks_on_disk()
        self.assertEqual(len(tasks), 1, "推送失败不得影响已落盘待办")
        self.assertIn("推送失败", result)
        self.assertIn("1 项已落任务列表", result)
        self.assertNotIn("TOKEN_", result)

    def test_empty_todos_pushes_report_only(self):
        """零待办轮:日报照推(待办段写"无"),不落任何任务"""
        args = _report_args(todos=[], total_mails=2)
        result, sender = self._submit(FakeSender(), args)

        text = sender.sent[0][1]["content"]["text"]
        self.assertIn("跨类别待办 0 项", text)
        self.assertIn("■ 待办\n无", text)
        self.assertEqual(self._tasks_on_disk(), [])
        self.assertIn("0 项已落任务列表", result)

    def test_empty_window_still_pushes_zero_report(self):
        """空收件箱轮:计头 共0封、四段全"无",日报照推(存活信号),不落任务"""
        args = _report_args(total_mails=0, todos=[], needs_reply=[], notices=[],
                            ignorable_count=0, ignorable_top_senders=[])
        result, sender = self._submit(FakeSender(), args)

        text = sender.sent[0][1]["content"]["text"]
        self.assertIn("共0封 · 跨类别待办 0 项", text)
        self.assertEqual(text.count("■ "), 4)
        self.assertEqual(self._tasks_on_disk(), [])
        self.assertIn("0 项已落任务列表", result)

    def test_domain_gate_denial_lands_todos_but_refuses_push(self):
        """域名门拒绝:本地落盘照常(门管的是网络),推送被拒并落审计"""
        from auditronclaw.core.tools import domain_gate
        # 三个名单来源全空:默认/环境变量/运行时审批规则(审批门 05 票起规则也是名单源)
        with patch.object(domain_gate, "DEFAULT_ALLOWED_DOMAINS", set()), \
             patch.object(domain_gate, "_EXTENDED_DOMAINS", set()), \
             patch.object(domain_gate, "load_approval_rule_domains", return_value=[]):
            result, sender = self._submit(FakeSender())

        self.assertEqual(self._tasks_on_disk().__len__(), 1)
        self.assertEqual(sender.sent, [], "被门拒绝时不得有真实发送")
        self.assertIn("白名单拦截", result)

    def test_structured_error_on_internal_failure(self):
        """内部异常兜底:结构化错误,不抛裸异常给 LLM,错误文案无凭据"""
        with patch("auditronclaw.core.tools.desk_tool.render_desk_report_text",
                   side_effect=RuntimeError(f"boom {self.secret_url}")):
            with InjectedSender(FakeSender()):
                result = self.submit_tool.invoke(_report_args())
        self.assertIn("失败", result)
        self.assertNotIn("TOKEN_", result)


class TestSubmitDeskReportShape(DeskToolTestCase):
    """工具形状:LLM 视角的参数面没有 URL/凭据字段;docstring 写明边界。"""

    def test_args_schema_is_structured_no_url_surface(self):
        schema = self.submit_tool.args_schema.model_json_schema()
        props = schema.get("properties", {})
        self.assertIn("todos", props)
        self.assertIn("needs_reply", props)
        self.assertIn("notices", props)
        forbidden = {"url", "webhook", "webhook_url", "sender"}
        self.assertEqual(set(props) & forbidden, set())

    def test_docstring_documents_boundary_and_rules(self):
        doc = self.submit_tool.description
        self.assertIn("域名", doc)
        self.assertIn("正交", doc, "docstring 要带分类规则(待办跨类别正交维度)")
        self.assertIn("空收件箱", doc, "docstring 要钉死空窗口同样提交(每日存活信号)")

    def test_registered_in_builtins(self):
        from auditronclaw.core.config import WorkspaceConfig
        from auditronclaw.core.tools.builtins import build_builtin_tools
        with tempfile.TemporaryDirectory() as tmp:
            cfg = WorkspaceConfig.from_root(tmp)
            names = {t.name for t in build_builtin_tools(cfg, "shape_probe")}
        self.assertIn("submit_mailbox_desk_report", names)


class TestCredentialNeverReachesAuditFile(DeskToolTestCase):
    """凭据纪律落盘级验证:提交一轮后,审计 jsonl 全文不含 webhook URL。"""

    def test_audit_file_clean_after_submit(self):
        from auditronclaw.core.logger import get_audit_logger
        self._submit(FakeSender())
        get_audit_logger().log_queue.join()
        with open(os.path.join(get_audit_logger().log_dir, "system.jsonl"), encoding="utf-8") as f:
            full_text = f.read()
        self.assertNotIn(self.secret_url, full_text)
        self.assertIn("事务台", full_text, "提交必须留可检索的审计事件")


if __name__ == '__main__':
    unittest.main()
