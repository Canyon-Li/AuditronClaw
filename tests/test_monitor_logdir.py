"""monitor 监听落点接线。

monitor 曾硬编码盯 PROJECT_ROOT/logs，而 logger 默认写 config.LOG_DIR
（WORKSPACE_DIR/logs，logger.py 单例构造即固化）——monitor 默认在监听一个
没人写入的目录。实证（2026-08-29）：root/logs 的 jsonl 最新写入停在
08-25 23:00，workspace/logs 当日活跃。本次只接线：落点改读 config，
不引入 WorkspaceConfig / 注入机制（配置装配期注入另行收编）。

断言只锚定 resolver 与 config.LOG_DIR 同源，不比较 monitor.LOG_FILE 固化值
与运行时 config——既有 test_lazy_loading 会 reload(config) 指向临时目录，
固化值与运行值必然漂移（装配期注入收编前的已知现象）。
"""
import os
import unittest

import entry.monitor as monitor
from auditronclaw.core import config


class TestMonitorLogDirWiring(unittest.TestCase):
    def test_log_file_path_anchors_at_config_log_dir(self):
        """落点锚定 config.LOG_DIR——与 logger 写侧同源，不随启动目录漂移。"""
        self.assertEqual(
            monitor.log_file_path("probe_thread"),
            os.path.join(config.LOG_DIR, "probe_thread.jsonl"),
        )

    def test_log_file_path_leaves_repo_root_logs(self):
        """回退哨兵：默认部署（workspace 未重定向）下不得再指回仓库根 logs/。"""
        repo_root_logs = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(monitor.__file__))), "logs")
        self.assertNotEqual(monitor.log_file_path("probe_thread"),
                            os.path.join(repo_root_logs, "probe_thread.jsonl"))


if __name__ == "__main__":
    unittest.main()
