import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

import yaml

import bench_pipeline


MAILBOX_SPEC = {
    "mails": [
        {
            "sender": "招商银行 <ccsvc@mail.creditcard.example>",
            "subject": "【账单提醒】您本期信用卡账单已出",
            "hours_ago": 2,
            "body": "尊敬的客户:您本期账单应还 ¥2,186.00,请于 3 日内完成还款。",
        },
    ]
}


class TestMailboxFixtureSeam(unittest.TestCase):
    """基准流水线的邮箱事务台注入缝:fixture 邮箱 + 假 sender + 占位凭据,零真实网络。"""

    def test_fixture_roundtrip_and_restore(self):
        """进入:fixture 可读、推送被捕获、凭据为占位值;退出:生产通道与原环境还原"""
        from auditronclaw.core.tools.feishu_tool import send_feishu_summary
        from auditronclaw.core.tools.mail_tool import read_recent_emails

        # 预置非占位值:退出后必须还原(而非清空)——占位凭据不得污染运行环境
        saved = {"MAIL_ACCOUNT": "pre-existing@example.com"}
        old_env = {k: os.environ.get(k) for k in bench_pipeline._BENCH_MAIL_ENV}
        os.environ.update(saved)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with bench_pipeline.mailbox_fixture(MAILBOX_SPEC, tmp) as capture:
                    mails = read_recent_emails.invoke({"hours": 24, "max_emails": 10})
                    self.assertIn("账单", mails)
                    self.assertIn("还款", mails)
                    self.assertEqual(os.environ["MAIL_ACCOUNT"],
                                     bench_pipeline._BENCH_MAIL_ENV["MAIL_ACCOUNT"])

                    receipt = send_feishu_summary.invoke({"summary_text": "日报测试文本"})
                    self.assertIn("成功", receipt)
                    self.assertEqual(capture.pushes, ["日报测试文本"])

            # 退出后从外部行为探针验证还原(不触私有字段):
            # 1. 环境还原原值;2. fixture 邮箱不再可读(邮箱回到"凭据未配置"的
            #    前置检查——真实 IMAP 通道不会读到测试账单);3. 推送不再进捕获器
            self.assertEqual(os.environ["MAIL_ACCOUNT"], saved["MAIL_ACCOUNT"])
            outside = read_recent_emails.invoke({"hours": 24, "max_emails": 10})
            self.assertNotIn("账单", outside)
            n_pushes = len(capture.pushes)
            send_feishu_summary.invoke({"summary_text": "退出后不应捕获"})
            self.assertEqual(len(capture.pushes), n_pushes)
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_fixture_dates_are_relative(self):
        """hours_ago 相对时间在运行期换算成 ISO 日期——用例文件不写绝对日期,基准任何时刻跑都在窗口内"""
        import json
        from datetime import datetime, timedelta

        with tempfile.TemporaryDirectory() as tmp:
            with bench_pipeline.mailbox_fixture(MAILBOX_SPEC, tmp):
                fixture_path = os.path.join(tmp, "bench_mailbox.json")
                with open(fixture_path, encoding="utf-8") as f:
                    mails = json.load(f)
        sent_at = datetime.fromisoformat(mails[0]["date"])
        self.assertAlmostEqual(sent_at, datetime.now() - timedelta(hours=2),
                               delta=timedelta(seconds=10))


class TestBenchCasesYaml(unittest.TestCase):
    """用例文件结构钉子:email 面与 mailbox_desk 面的就位形态(防漂移)。"""

    ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks'))

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(cls.ROOT, "cases", "injection_cases.yaml"), encoding="utf-8") as f:
            cls.injection = yaml.safe_load(f)
        with open(os.path.join(cls.ROOT, "cases", "golden_cases.yaml"), encoding="utf-8") as f:
            cls.golden = yaml.safe_load(f)

    def test_ids_unique_per_file(self):
        for cases, name in ((self.injection, "injection"), (self.golden, "golden")):
            ids = [c["id"] for c in cases]
            self.assertEqual(len(ids), len(set(ids)), f"{name} 用例 id 必须全局唯一")

    def test_email_surface_cases_have_fixture_mailbox(self):
        """email 面每条用例都有 fixture 邮箱,且恶意邮件混在至少 1 封正常邮件中间"""
        email_cases = [c for c in self.injection if c["surface"] == "email"]
        self.assertGreaterEqual(len(email_cases), 6, "email 面用例数不足")
        for case in email_cases:
            with self.subTest(case_id=case["id"]):
                mails = case["setup"]["mailbox"]["mails"]
                self.assertGreaterEqual(len(mails), 2, "载荷必须嵌在正常邮件中间,不能单封裸投")
                for m in mails:
                    self.assertIn("hours_ago", m, "邮件时间用相对量,不写绝对日期")
                    self.assertTrue(m["body"].strip(), "邮件正文不得为空")
                self.assertTrue(case.get("asserts"), "注入用例必须有行为断言")

    def test_golden_mailbox_desk_cases_double_anchored(self):
        """事务台 golden 用例:双锚(工具调用 + 落盘终态)+ 推送捕获断言"""
        desk_cases = [c for c in self.golden if c["surface"] == "mailbox_desk"]
        self.assertGreaterEqual(len(desk_cases), 2, "mailbox_desk 面用例数不足")
        for case in desk_cases:
            with self.subTest(case_id=case["id"]):
                assert_types = {a["type"] for a in case["asserts"]}
                self.assertIn("required_tool_call", assert_types)
                tools = {a.get("tool") for a in case["asserts"] if a["type"] == "required_tool_call"}
                self.assertIn("read_recent_emails", tools, "事务台必须从命名取信工具读取")
                self.assertIn("submit_mailbox_desk_report", tools, "事务台必须以结构化提交工具收口")
                self.assertIn("file_content", assert_types, "待办落盘必须有终态锚(tasks.json)")
                self.assertIn("push_contains", assert_types, "推送内容必须有捕获锚")
                self.assertTrue(case["setup"]["mailbox"]["mails"], "事务台用例需要 fixture 邮箱")


if __name__ == "__main__":
    unittest.main()
