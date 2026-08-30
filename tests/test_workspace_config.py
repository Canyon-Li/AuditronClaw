"""工作区配置(05 票):路径从 import 期常量改为装配期对象。

WorkspaceConfig 是全部 workspace 落点的唯一形状:入口构造一次、显式注入,
消费者不再各自 from-import 路径拷贝。from_env 是唯一读
AUDITRONCLAW_WORKSPACE 的地方——装配期路径不从 __file__ 推导仓库结构
(pip 装进 site-packages 后前提破裂,设计约束),因此工作区根必须显式给出
(环境变量),缺失即拒绝启动:宁可启动失败,不静默落到臆测位置。
"""
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError

from auditronclaw.core.config import WorkspaceConfig


class TestWorkspaceConfigFromRoot(unittest.TestCase):

    def test_from_root_derives_layout(self):
        """from_root 派生固定布局:数据库/记忆/工位/技能/任务/规则/日志各归其位"""
        with tempfile.TemporaryDirectory() as root:
            cfg = WorkspaceConfig.from_root(root)
            self.assertEqual(cfg.root, root)
            self.assertEqual(cfg.db_path, os.path.join(root, "state.sqlite3"))
            self.assertEqual(cfg.memory_dir, os.path.join(root, "memory"))
            self.assertEqual(cfg.office_dir, os.path.join(root, "office"))
            self.assertEqual(cfg.skills_dir, os.path.join(root, "office", "skills"))
            self.assertEqual(cfg.tasks_file, os.path.join(root, "tasks.json"))
            self.assertEqual(cfg.approval_rules_file,
                             os.path.join(root, "approval_rules.json"))
            self.assertEqual(cfg.log_dir, os.path.join(root, "logs"))

    def test_frozen(self):
        """frozen:装配后路径不可改——注入的是值快照,不是活引用"""
        with tempfile.TemporaryDirectory() as root:
            cfg = WorkspaceConfig.from_root(root)
            with self.assertRaises(FrozenInstanceError):
                cfg.tasks_file = os.path.join(root, "elsewhere.json")


class TestWorkspaceConfigFromEnv(unittest.TestCase):

    _ENV_KEY = "AUDITRONCLAW_WORKSPACE"

    def test_from_env_reads_env(self):
        """from_env 读 AUDITRONCLAW_WORKSPACE,派生同 from_root"""
        with tempfile.TemporaryDirectory() as root:
            old = os.environ.get(self._ENV_KEY)
            os.environ[self._ENV_KEY] = root
            try:
                self.assertEqual(WorkspaceConfig.from_env(),
                                 WorkspaceConfig.from_root(root))
            finally:
                if old is None:
                    os.environ.pop(self._ENV_KEY, None)
                else:
                    os.environ[self._ENV_KEY] = old

    def test_from_env_requires_explicit_workspace(self):
        """缺省即拒:不从 __file__ 或 cwd 臆测仓库结构——显式 env 是唯一默认"""
        old = os.environ.pop(self._ENV_KEY, None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                WorkspaceConfig.from_env()
            self.assertIn(self._ENV_KEY, str(ctx.exception))
        finally:
            if old is not None:
                os.environ[self._ENV_KEY] = old

    def test_from_env_ignores_cwd(self):
        """cwd 不参与取根:同 env 下换任何启动目录,配置逐字段相等"""
        with tempfile.TemporaryDirectory() as root, \
                tempfile.TemporaryDirectory() as elsewhere:
            old_env = os.environ.get(self._ENV_KEY)
            old_cwd = os.getcwd()
            os.environ[self._ENV_KEY] = root
            try:
                os.chdir(elsewhere)
                self.assertEqual(WorkspaceConfig.from_env(),
                                 WorkspaceConfig.from_root(root))
            finally:
                os.chdir(old_cwd)
                if old_env is None:
                    os.environ.pop(self._ENV_KEY, None)
                else:
                    os.environ[self._ENV_KEY] = old_env


class TestWorkspaceConfigEnsureDirs(unittest.TestCase):

    def test_ensure_dirs_creates_layout(self):
        """ensure_dirs 建齐运行目录(根/记忆/工位/技能);日志目录归 logger 自检"""
        with tempfile.TemporaryDirectory() as root:
            cfg = WorkspaceConfig.from_root(os.path.join(root, "ws"))
            cfg.ensure_dirs()
            for d in (cfg.root, cfg.memory_dir, cfg.office_dir, cfg.skills_dir):
                self.assertTrue(os.path.isdir(d), f"{d} 应已就绪")

    def test_ensure_dirs_idempotent(self):
        """重复调用无害"""
        with tempfile.TemporaryDirectory() as root:
            cfg = WorkspaceConfig.from_root(root)
            cfg.ensure_dirs()
            cfg.ensure_dirs()


if __name__ == "__main__":
    unittest.main()
