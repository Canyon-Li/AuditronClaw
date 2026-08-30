"""monitor 监听落点接线。

monitor 曾硬编码盯 PROJECT_ROOT/logs，而 logger 写侧落 workspace/logs
——monitor 默认在监听一个没人写入的目录（实证 2026-08-29：root/logs 的
jsonl 停在 08-25，workspace/logs 当日活跃）。05 票收编为装配期注入：
log_file_path(log_dir, thread_id) 收装配入参，main 从 WorkspaceConfig
.from_env() 取与入口同一工作区——读写两侧同锚，不随启动目录漂移。
"""
import os
import unittest
from unittest.mock import patch

import entry.monitor as monitor
from auditronclaw.core.config import WorkspaceConfig


class TestMonitorLogDirWiring(unittest.TestCase):
    def test_log_file_path_anchors_at_given_log_dir(self):
        """落点锚定装配入参——与 logger 写侧（入口注入 cfg.log_dir）同源。"""
        with patch.dict(os.environ, {"AUDITRONCLAW_WORKSPACE": "W"}):
            cfg = WorkspaceConfig.from_env()
        self.assertEqual(
            monitor.log_file_path(cfg.log_dir, "probe_thread"),
            os.path.join(cfg.log_dir, "probe_thread.jsonl"),
        )

    def test_log_file_path_leaves_repo_root_logs(self):
        """回退哨兵：落点出自工作区，不得再指回仓库根 logs/。"""
        repo_root_logs = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(monitor.__file__))),
            "logs")
        self.assertNotEqual(
            monitor.log_file_path(os.path.join("W", "logs"), "probe_thread"),
            os.path.join(repo_root_logs, "probe_thread.jsonl"))


if __name__ == "__main__":
    unittest.main()
