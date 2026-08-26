"""审计加固三行为:日志目录锚定、写失败兜底、启动自检。

只测外部行为(文件落在哪、文件里有什么、构造是否抛异常),不测线程
结构与队列内部。单例重置是测试手法:换一个全新 logger 跑用例,退出时
恢复原单例,不污染其他测试。
"""
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auditronclaw.core.logger import JSONLEventLogger


class SingletonResetTestCase(unittest.TestCase):
    """测试期间换新单例,收尾恢复。

    测试实例的写线程不 shutdown——杀掉后它注册的 atexit 再 join 会
    永久挂起(test_domain_gate_tools 有先例注释);泄漏的守护线程由
    各自的 atexit 处理器在进程退出时正常收尾。
    """

    def setUp(self):
        self._saved_instance = JSONLEventLogger._instance
        JSONLEventLogger._instance = None

    def tearDown(self):
        JSONLEventLogger._instance = self._saved_instance


class TestLogDirAnchoring(SingletonResetTestCase):

    def test_log_dir_anchored_to_workspace_regardless_of_cwd(self):
        """锚定:cwd 切到任意目录再初始化,日志仍落 WORKSPACE_DIR/logs"""
        with tempfile.TemporaryDirectory() as workspace, \
                tempfile.TemporaryDirectory() as elsewhere:
            old_env = os.environ.get("AUDITRONCLAW_WORKSPACE")
            old_cwd = os.getcwd()
            os.environ["AUDITRONCLAW_WORKSPACE"] = workspace
            import auditronclaw.core.config as config_module
            importlib.reload(config_module)
            try:
                os.chdir(elsewhere)
                # 不传 log_dir:默认位置必须出自 config.LOG_DIR,而非 cwd 相对路径
                logger = JSONLEventLogger()
                marker = "anchoring-probe-8f2c"
                logger.log_event("system", "audit_hardening_test", marker=marker)
                logger.log_queue.join()

                anchored = os.path.join(workspace, "logs", "system.jsonl")
                self.assertTrue(os.path.exists(anchored),
                                "审计事件应落 WORKSPACE_DIR/logs,与启动目录无关")
                with open(anchored, encoding="utf-8") as f:
                    self.assertIn(marker, f.read())
                self.assertFalse(os.path.exists(os.path.join(elsewhere, "logs")),
                                 "不得在启动目录下另起 logs/")
            finally:
                os.chdir(old_cwd)
                if old_env is None:
                    os.environ.pop("AUDITRONCLAW_WORKSPACE", None)
                else:
                    os.environ["AUDITRONCLAW_WORKSPACE"] = old_env
                importlib.reload(config_module)


class TestWriteFailureFallback(SingletonResetTestCase):

    def test_main_write_failure_lands_in_fallback_file(self):
        """主写失败:事件落同目录 audit_fallback.jsonl,同格式 JSONL 且附失败缘由"""
        with tempfile.TemporaryDirectory() as tmp:
            logger = JSONLEventLogger(log_dir=os.path.join(tmp, "logs"))
            # 把主日志文件路径做成目录,逼 open 失败
            os.makedirs(os.path.join(logger.log_dir, "system.jsonl"))
            marker = "fallback-probe-3d71"
            logger.log_event("system", "audit_hardening_test", marker=marker)
            logger.log_queue.join()

            fallback = os.path.join(logger.log_dir, "audit_fallback.jsonl")
            self.assertTrue(os.path.exists(fallback), "主写失败的事件必须可发现,不得静默丢弃")
            with open(fallback, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            hit = next(l for l in lines if l.get("marker") == marker)
            self.assertEqual(hit["event"], "audit_hardening_test", "兜底行保持事件原字段")
            self.assertTrue(hit.get("fallback_reason"), "兜底行须附失败缘由")

    def test_unserializable_event_lands_in_fallback_file(self):
        """主写因不可序列化值失败:事件仍落兜底(宽容序列化),不静默丢弃"""
        with tempfile.TemporaryDirectory() as tmp:
            logger = JSONLEventLogger(log_dir=os.path.join(tmp, "logs"))
            logger.log_event("system", "audit_hardening_test",
                             marker="unserializable-9f2a", payload=object())
            logger.log_queue.join()

            fallback = os.path.join(logger.log_dir, "audit_fallback.jsonl")
            self.assertTrue(os.path.exists(fallback), "不可序列化事件也必须留可发现痕迹")
            with open(fallback, encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            hit = next(l for l in lines if l.get("marker") == "unserializable-9f2a")
            self.assertEqual(hit["event"], "audit_hardening_test", "兜底行保持事件原字段")
            self.assertTrue(hit.get("fallback_reason"), "兜底行须附失败缘由")

    def test_fallback_failure_prints_and_thread_survives(self):
        """兜底也失败:打印错误收场,写线程不炸"""
        with tempfile.TemporaryDirectory() as tmp:
            logger = JSONLEventLogger(log_dir=os.path.join(tmp, "logs"))
            os.makedirs(os.path.join(logger.log_dir, "system.jsonl"))
            os.makedirs(os.path.join(logger.log_dir, "audit_fallback.jsonl"))
            sink = io.StringIO()
            with redirect_stdout(sink):
                logger.log_event("system", "audit_hardening_test", marker="doom")
                logger.log_queue.join()
            self.assertIn("[Logger Error]", sink.getvalue(), "灾难场景打印是诚实极限")
            self.assertTrue(logger.worker_thread.is_alive(), "写线程不得因写失败死亡")


class TestStartupSelfCheck(SingletonResetTestCase):

    def test_refuses_to_start_when_log_dir_is_a_file(self):
        """LOG_DIR 路径被普通文件占住:构造即抛,拒绝启动;失败不留半初始化单例"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "logs")
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("not a directory")
            with self.assertRaises(RuntimeError):
                JSONLEventLogger(log_dir=blocker)
            # 第二次构造仍拒绝——失败不得留下半初始化单例供后续调用取用
            with self.assertRaises(RuntimeError):
                JSONLEventLogger(log_dir=blocker)

    def test_refuses_to_start_when_probe_fails(self):
        """探针文件路径被目录占住:探针写读失败,构造即抛"""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = os.path.join(tmp, "logs")
            os.makedirs(os.path.join(log_dir, f".startup_probe.{os.getpid()}"))
            with self.assertRaises(RuntimeError):
                JSONLEventLogger(log_dir=log_dir)


if __name__ == '__main__':
    unittest.main()
