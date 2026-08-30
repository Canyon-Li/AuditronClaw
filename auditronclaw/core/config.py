"""工作区配置(05 票):路径是装配期对象,不是 import 期常量。

WorkspaceConfig 是全部 workspace 落点的唯一形状;入口(main/bench)构造一次、
显式注入各消费者。from_env 是唯一读 AUDITRONCLAW_WORKSPACE 的地方——
装配期路径不从 __file__ 推导仓库结构(pip 装进 site-packages 后前提破裂),
工作区根必须显式给出,缺失即拒绝启动:宁可启动失败,不静默落到臆测位置。
"""
import os
from dataclasses import dataclass

_ENV_WORKSPACE = "AUDITRONCLAW_WORKSPACE"


@dataclass(frozen=True)
class WorkspaceConfig:
    """工作区落点快照:构造即固化,注入的是值不是活引用。

    布局固定:office 恒为 root/office(前缀剥除规则、规则作用域命名空间
    都以这个布局为前提);技能卡槽在 office/skills;审批规则在 workspace 级
    (office 之外——agent 的写面够不着自己的规则)。
    """

    root: str
    db_path: str              # 状态机:潜意识与短期记忆
    memory_dir: str           # 显性记忆:Markdown 画像
    office_dir: str           # 沙盒工位:唯一被允许执行文件与 shell 操作的空间
    skills_dir: str           # 技能卡槽(office 内)
    tasks_file: str           # 定时任务队列文件
    approval_rules_file: str  # 审批规则文件(workspace 级、office 外)
    log_dir: str              # 审计日志唯一落点:锚定 workspace,不随启动目录漂移

    @classmethod
    def from_root(cls, root: str) -> "WorkspaceConfig":
        """从工作区根派生固定布局。"""
        office = os.path.join(root, "office")
        return cls(
            root=root,
            db_path=os.path.join(root, "state.sqlite3"),
            memory_dir=os.path.join(root, "memory"),
            office_dir=office,
            skills_dir=os.path.join(office, "skills"),
            tasks_file=os.path.join(root, "tasks.json"),
            approval_rules_file=os.path.join(root, "approval_rules.json"),
            log_dir=os.path.join(root, "logs"),
        )

    @classmethod
    def from_env(cls) -> "WorkspaceConfig":
        """装配期入口:唯一读 AUDITRONCLAW_WORKSPACE 的地方。

        缺省即拒(fail-fast):不 fallback 到 __file__ 推导的仓库默认——
        源码检出请在 .env 或环境里显式指定工作区根。
        """
        root = os.environ.get(_ENV_WORKSPACE)
        if not root:
            raise RuntimeError(
                f"未设置 {_ENV_WORKSPACE}:工作区根必须显式指定"
                "(源码检出请在 .env 设置,如 AUDITRONCLAW_WORKSPACE=<仓库>/workspace)。"
                "不从安装位置或启动目录臆测。"
            )
        return cls.from_root(root)

    def ensure_dirs(self) -> None:
        """建齐运行目录(根/记忆/工位/技能);日志目录归 logger 启动自检创建。"""
        for d in (self.root, self.memory_dir, self.office_dir, self.skills_dir):
            os.makedirs(d, exist_ok=True)
