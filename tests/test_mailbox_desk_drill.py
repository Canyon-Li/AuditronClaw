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

from helpers import FakeSender, InjectedProvider, InjectedSender

from auditronclaw.core.tools.feishu_tool import send_feishu_summary
from auditronclaw.core.tools.mail_tool import read_recent_emails
from auditronclaw.core.tools.builtins import create_task_tools


# ============ 事务台部署演练:推送失败路径(邮箱事务台部署接线)============
#
# 部署验收项:"断网/错 URL 时待办仍落盘、错误结构化可查、下轮不受污染"。
# 演练按部署顺序跑一轮完整事务台的工具序列(读取 → 待办落盘 → 推送),
# 取信层与推送层走注入点 B,全程零真实网络:
# 1. 推送在待办落盘之后失败,待办必须已在 tasks.json(顺序即保障);
# 2. 失败返回结构化错误并落审计事件,不抛裸异常;
# 3. 恢复网络后下一轮推送正常,失败不留任何污染状态;
# 4. 占位凭据不落审计日志全文(部署纪律:凭据只存在宿主机 .env)。


def _broken_sender():
    """断网形态的假发送器:URLError,消息里内嵌 URL(凭据泄露路径演练)。"""
    return FakeSender(error=URLError("cannot reach the webhook url"))


class TestDeskRoundPushFailureDrill(unittest.TestCase):
    """断网演练:一轮事务台里推送失败,待办与下一轮都不受影响。"""

    def setUp(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(path)
        self.temp_path = path
        # 队列落点为装配入参(05 票):工具工厂吃临时文件路径
        self.tools = {t.name: t for t in create_task_tools(path)}

        # 占位凭据:断言"不落审计日志全文"时用唯一串,缺席才有意义
        self.secret_url = (
            f"https://open.feishu.cn/open-apis/bot/v2/hook/TOKEN_{uuid.uuid4().hex[:12]}"
        )
        self.env = {
            "MAIL_ACCOUNT": "me@qq.com",
            "MAIL_IMAP_PASSWORD": f"SECRET_AUTH_{uuid.uuid4().hex[:8]}",
        }

    def tearDown(self):
        if os.path.exists(self.temp_path):
            os.unlink(self.temp_path)

    def _bill_fixture_provider(self):
        """账单邮件 fixture(部署演练形态,与 golden gold_desk_001 同源)。"""
        mails = [{
            "sender": "招商银行 <ccsvc@mail.creditcard.example>",
            "subject": "【账单提醒】您本期信用卡账单已出",
            "date": datetime.now() - timedelta(hours=2),
            "body": "尊敬的客户:您本期账单应还 ¥2,186.00,最后还款日 2026-08-26,请及时还款。",
        }]

        def provider(config, hours, max_emails):
            return list(mails)

        return provider

    def test_push_failure_keeps_todos_and_next_round_clean(self):
        # ---- 第 1 步:读取(fixture 通道,零网络) ----
        with InjectedProvider(self._bill_fixture_provider()), patch.dict(os.environ, self.env):
            read = read_recent_emails.invoke({"hours": 24, "max_emails": 10})
        self.assertIn("外部数据区", read)
        self.assertIn("账单提醒", read)

        # ---- 第 2 步:待办落盘(先于推送——顺序即"推送失败不吞待办"的保障) ----
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        scheduled = self.tools["schedule_task"].invoke({
            "target_time": tomorrow,
            "description": "还信用卡账单 ¥2,186.00,截止 2026-08-26(来源:招行账单提醒邮件)",
        })
        self.assertIn("成功", scheduled)

        # ---- 第 3 步:推送失败(断网形态:URLError 消息内嵌 URL) ----
        broken_sender = _broken_sender()
        with InjectedSender(broken_sender), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=self.secret_url,
        ):
            failed_push = send_feishu_summary.invoke({"summary_text": "邮箱事务台日报(演练)"})

        self.assertIn("推送失败", failed_push, "失败必须返回结构化错误,不是成功也不是异常")
        self.assertNotIn("TOKEN_", failed_push, "错误文案不得内嵌 webhook URL")

        # ---- 待办仍落盘:tasks.json 里账单待办原样在 ----
        with open(self.temp_path, encoding="utf-8") as f:
            tasks = json.load(f)
        self.assertTrue(any("还款" in t.get("description", "") or "账单" in t.get("description", "")
                            for t in tasks), "推送失败不得影响已落盘待办")

        # ---- 第 4 步:下一轮推送正常(网络恢复,失败不留污染状态) ----
        healed_sender = FakeSender()
        with InjectedSender(healed_sender), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=self.secret_url,
        ):
            ok_push = send_feishu_summary.invoke({"summary_text": "邮箱事务台日报(重试轮)"})

        self.assertIn("推送成功", ok_push)
        self.assertEqual(len(healed_sender.sent), 1, "恢复后一轮恰好推送一次")
        self.assertIn("邮箱事务台日报", healed_sender.sent[0][1]["content"]["text"])

    def test_push_failure_leaves_queryable_audit_and_no_credentials(self):
        """错误结构化可查:审计事件可检索;凭据(占位 URL)不落日志全文"""
        broken_sender = _broken_sender()
        with InjectedSender(broken_sender), patch(
            "auditronclaw.core.tools.feishu_tool.get_feishu_webhook_url",
            return_value=self.secret_url,
        ), patch.dict(os.environ, self.env):
            send_feishu_summary.invoke({"summary_text": "邮箱事务台日报(演练)"})

        # 等异步队列 flush 到 jsonl,再扫 system 级日志全文
        from auditronclaw.core.logger import get_audit_logger
        get_audit_logger().log_queue.join()
        with open(os.path.join(get_audit_logger().log_dir, "system.jsonl"), encoding="utf-8") as f:
            full_text = f.read()

        self.assertIn("飞书推送失败", full_text, "失败必须留可检索的审计事件")
        self.assertNotIn(self.secret_url, full_text, "webhook URL 不得落审计日志")
        self.assertNotIn(self.env["MAIL_IMAP_PASSWORD"], full_text, "邮箱授权码不得落审计日志")


if __name__ == '__main__':
    unittest.main()
