"""审计加固行为:落点装配期锚定、写失败兜底、启动自检、工厂语义。

只测外部行为(文件落在哪、文件里有什么、构造是否抛异常),不测线程
结构与队列内部。审计 logger 是装配期对象(05 票):入口 init_audit_logger
构造一次,测试直接构造实例(不 shutdown 写线程——杀掉后它注册的 atexit
再 join 会永久挂起,泄漏的守护线程由各自的 atexit 处理器在进程退出时
正常收尾,先例:test_domain_gate_tools)。
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
from auditronclaw.core import logger as logger_module


class TestLogDirAnchoring(unittest.TestCase):

    def test_log_dir_anchored_to_workspace_regardless_of_cwd(self):
        """锚定:cwd 切到任意目录,审计仍落装配给定的工作区 logs/"""
        with tempfile.TemporaryDirectory() as workspace, \
                tempfile.TemporaryDirectory() as elsewhere:
            old_env = os.environ.get("AUDITRONCLAW_WORKSPACE")
            old_cwd = os.getcwd()
            os.environ["AUDITRONCLAW_WORKSPACE"] = workspace
            import auditronclaw.core.config as config_module
            importlib.reload(config_module)
            try:
                os.chdir(elsewhere)
                # 落点出自装配期配置(WorkspaceConfig.log_dir),而非 cwd 相对路径
                logger = JSONLEventLogger(log_dir=config_module.WorkspaceConfig
                                          .from_env().log_dir)
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


class TestWriteFailureFallback(unittest.TestCase):

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
        """兜底也失败:打印错误收场,写线程不崩溃"""
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


class TestStartupSelfCheck(unittest.TestCase):

    def test_refuses_to_start_when_log_dir_is_a_file(self):
        """LOG_DIR 路径被普通文件占住:构造即抛,拒绝启动"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "logs")
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("not a directory")
            # 第二次构造仍拒绝——每次构造都重新自检
            for _ in range(2):
                with self.assertRaises(RuntimeError):
                    JSONLEventLogger(log_dir=blocker)

    def test_refuses_to_start_when_probe_fails(self):
        """探针文件路径被目录占住:探针写读失败,构造即抛"""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = os.path.join(tmp, "logs")
            os.makedirs(os.path.join(log_dir, f".startup_probe.{os.getpid()}"))
            with self.assertRaises(RuntimeError):
                JSONLEventLogger(log_dir=log_dir)


class TestFactorySemantics(unittest.TestCase):
    """装配期工厂语义:初始化一次、幂等、换址拒绝、未初始化取用即拒。"""

    def setUp(self):
        # 测试手法:换掉进程实例,退出恢复(conftest 的会话锚不受污染)
        self._saved = logger_module._audit_logger
        logger_module._audit_logger = None

    def tearDown(self):
        logger_module._audit_logger = self._saved

    def test_get_before_init_refuses(self):
        """未初始化即取用:拒绝——无审计不运行"""
        with self.assertRaises(RuntimeError):
            logger_module.get_audit_logger()

    def test_init_is_idempotent_for_same_dir(self):
        """同落点重复初始化:返回既有实例,不重建、不换线程"""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = os.path.join(tmp, "logs")
            first = logger_module.init_audit_logger(log_dir)
            second = logger_module.init_audit_logger(log_dir)
            self.assertIs(first, second)
            self.assertIs(logger_module.get_audit_logger(), first)

    def test_init_refuses_to_move_anchor(self):
        """换落点初始化:拒绝——审计位置装配后固化,静默换址即凭证失联"""
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            logger_module.init_audit_logger(os.path.join(tmp1, "logs"))
            with self.assertRaises(RuntimeError):
                logger_module.init_audit_logger(os.path.join(tmp2, "logs"))

    def test_failed_init_leaves_no_instance(self):
        """构造失败不留半初始化实例:下次初始化重新走自检"""
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "logs")
            with open(blocker, "w", encoding="utf-8") as f:
                f.write("not a directory")
            with self.assertRaises(RuntimeError):
                logger_module.init_audit_logger(blocker)
            self.assertIsNone(logger_module._audit_logger)
            good = logger_module.init_audit_logger(os.path.join(tmp, "logs_ok"))
            self.assertIs(logger_module.get_audit_logger(), good)


if __name__ == '__main__':
    unittest.main()
