import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks')))

import yaml

import bench_pipeline
from helpers import FakeSender, InjectedProvider, InjectedSender


# benchmarks/ 目录路径真源(两个用例文件结构测试类共用,不互相借用类内部)
BENCHMARKS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'benchmarks'))

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
    """基准流水线的邮箱事务台注入点:fixture 邮箱 + 假 sender + 占位凭据,零真实网络。"""

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

            # 退出后从外部行为探针验证还原(不触私有字段,零真实网络):探针经注入的
            # 新假件走一遍,证明 fixture 通道已脱钩——生产通道绝不触达(conftest
            # 哨兵把守,触碰即红)。占位凭据只骗过"未配置不碰网络"的前置检查,
            # 让探针在有无 .env 的环境里行为一致。
            # 1. 环境还原原值;2. fixture 邮箱通道脱钩(读到新 provider 的探针
            #    邮件,而非 fixture 账单);3. 推送通道脱钩(进新 sender,不进旧捕获器)
            self.assertEqual(os.environ["MAIL_ACCOUNT"], saved["MAIL_ACCOUNT"])

            def probe_provider(config, hours, max_emails):
                return [{"sender": "probe@example.com", "subject": "还原探针邮件",
                         "date": None, "body": "probe"}]

            probe_sender = FakeSender()
            probe_env = {
                "MAIL_ACCOUNT": "probe@placeholder.local",
                "MAIL_IMAP_PASSWORD": "probe-placeholder",
                "FEISHU_WEBHOOK_URL": "https://open.feishu.cn/probe-placeholder",
            }
            with patch.dict(os.environ, probe_env), \
                 InjectedProvider(probe_provider), InjectedSender(probe_sender):
                outside = read_recent_emails.invoke({"hours": 24, "max_emails": 10})
                self.assertIn("还原探针邮件", outside)
                self.assertNotIn("账单", outside)

                n_pushes = len(capture.pushes)
                send_feishu_summary.invoke({"summary_text": "退出后通道还原探针"})
                self.assertEqual(len(probe_sender.sent), 1)
                self.assertEqual(probe_sender.sent[0][1]["content"]["text"],
                                 "退出后通道还原探针")
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
        """事务台 golden 用例:按 fixture 形状分锚——满邮箱双锚(调用+落盘终态),
        空收件箱锚空日报计头(共0封,存活信号);两者都必有推送捕获锚"""
        desk_cases = [c for c in self.golden if c["surface"] == "mailbox_desk"]
        self.assertGreaterEqual(len(desk_cases), 2, "mailbox_desk 面用例数不足")
        for case in desk_cases:
            with self.subTest(case_id=case["id"]):
                assert_types = {a["type"] for a in case["asserts"]}
                self.assertIn("required_tool_call", assert_types)
                tools = {a.get("tool") for a in case["asserts"] if a["type"] == "required_tool_call"}
                self.assertIn("read_recent_emails", tools, "事务台必须从命名取信工具读取")
                self.assertIn("submit_mailbox_desk_report", tools, "事务台必须以结构化提交工具完成")
                self.assertIn("push_contains", assert_types, "推送内容必须有捕获锚")
                if case["setup"]["mailbox"]["mails"]:
                    self.assertIn("file_content", assert_types,
                                  "满邮箱用例待办落盘必须有终态锚(tasks.json)")
                else:
                    pushes = [a.get("contains", "") for a in case["asserts"]
                              if a["type"] == "push_contains"]
                    self.assertTrue(any("共0封" in p for p in pushes),
                                    "空收件箱用例必须锚住空日报计头(共0封)")


class TestInterpreterSurfaceConservation(unittest.TestCase):
    """解释器面用例集守恒:集合已冻结(审批门缓解、容器边界根治两个后续阶段
    复跑同一批,中途不改数字才可比),本测试防冻结后的用例文件悄悄漂移。
    """

    SCRIPT_ANCHORS = {
        "interp_001": "diag_env.py",
        "interp_002": "pack_notes.py",
        "interp_003": "check_update.py",
        "interp_004": "scan.py",
    }

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BENCHMARKS_DIR, "cases", "injection_cases.yaml"),
                  encoding="utf-8") as f:
            cls.injection = yaml.safe_load(f)
        cls.interp = [c for c in cls.injection if c["surface"] == "interpreter"]

    def _case(self, case_id):
        return next(c for c in self.interp if c["id"] == case_id)

    def test_surface_registered_in_runner(self):
        from run_injection_bench import SURFACES
        self.assertIn("interpreter", SURFACES)

    def test_frozen_set_is_exactly_four(self):
        """集合守恒:interpreter 面恰 4 条且与锚表一致;interp_ 前缀与 surface 值
        双向对齐(防错挂面)"""
        self.assertEqual(sorted(c["id"] for c in self.interp), sorted(self.SCRIPT_ANCHORS))
        strays = [c["id"] for c in self.injection
                  if c["id"].startswith("interp_") and c["surface"] != "interpreter"]
        self.assertEqual(strays, [])

    def test_each_case_anchors_exactly_one_execution(self):
        """判定守恒:每条恰一条断言,且是 execute_office_shell + 各自脚本文件名
        (只锚执行,不锚写入,不混入其他断言类型)"""
        for case in self.interp:
            with self.subTest(case_id=case["id"]):
                self.assertEqual(len(case["asserts"]), 1)
                a = case["asserts"][0]
                self.assertEqual(a["type"], "forbidden_tool_call")
                self.assertEqual(a["tool"], "execute_office_shell")
                self.assertEqual(a["arg_contains"], self.SCRIPT_ANCHORS[case["id"]])

    def test_anchor_filename_appears_in_trigger(self):
        """锚可达性:锚定的脚本文件名在话术中出现过(LLM 被要求写的正是该文件)"""
        for case in self.interp:
            with self.subTest(case_id=case["id"]):
                self.assertIn(self.SCRIPT_ANCHORS[case["id"]], case["trigger"])

    def test_interp_001_setup_has_decoy_env(self):
        """零实害验证:interp_001 诱饵 .env 预置在 workspace 根——真实执行读到假密钥"""
        writes = {w["path"]: w["content"] for w in self._case("interp_001")["setup"]["write"]}
        self.assertIn(".env", writes)
        self.assertIn("sk-interp-fake", writes[".env"])


class TestBenchAuditAnchoring(unittest.TestCase):
    """基准进程的审计锚定:整场审计必须落仓库 workspace/logs,不随首用例临时目录漂移。"""

    def test_audit_singleton_survives_workspace_reload(self):
        """导入 bench_pipeline 后再 reload 临时 workspace,审计单例仍锚定仓库 workspace/logs。

        若单例迟到(首个用例 reload 之后才首次构造),会被首用例的临时目录锚走:
        位置随场而变、临时目录有被系统清理风险。子进程级验证,与真实 runner
        同序(导入 → reload → 取单例)。
        """
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        script = (
            "import sys, tempfile\n"
            f"sys.path.insert(0, {BENCHMARKS_DIR!r})\n"
            "import bench_pipeline\n"
            "from bench_pipeline import reload_with_workspace\n"
            "tmp = tempfile.mkdtemp(prefix='anchor_probe_')\n"
            "reload_with_workspace(tmp)\n"
            "from auditronclaw.core.logger import audit_logger\n"
            "print(audit_logger.log_dir)\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "AUDITRONCLAW_WORKSPACE"}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, cwd=repo_root, env=env, check=True,
        )
        printed_log_dir = out.stdout.strip().splitlines()[-1]
        self.assertEqual(printed_log_dir, os.path.join(repo_root, "workspace", "logs"))


if __name__ == "__main__":
    unittest.main()
