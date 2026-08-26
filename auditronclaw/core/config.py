import os
from dotenv import load_dotenv

load_dotenv()

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(CORE_DIR)
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)

WORKSPACE_DIR = os.getenv("AUDITRONCLAW_WORKSPACE", os.path.join(PROJECT_ROOT, "workspace"))


DB_PATH = os.path.join(WORKSPACE_DIR, "state.sqlite3")     # 状态机：潜意识与短期记忆
MEMORY_DIR = os.path.join(WORKSPACE_DIR, "memory")         # 显性记忆：Markdown 画像
PERSONAS_DIR = os.path.join(WORKSPACE_DIR, "personas")     # 人设区：系统 Prompt
SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")       # 脚本区：自动化武器库
OFFICE_DIR = os.path.join(WORKSPACE_DIR, "office")         # 沙盒工位 唯一被允许执行文件与shell操作的空间
SKILLS_DIR = os.path.join(OFFICE_DIR, "skills")            # 技能卡槽
TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")
# 审批规则文件：workspace 级、office 之外——agent 的写面被路径校验挡在
# office 内，够不着自己的规则（冷启动即无此文件=空规则集）
APPROVAL_RULES_FILE = os.path.join(WORKSPACE_DIR, "approval_rules.json")
LOG_DIR = os.path.join(WORKSPACE_DIR, "logs")            # 审计日志唯一落点：锚定 workspace，不随启动目录漂移

for d in [WORKSPACE_DIR, MEMORY_DIR, PERSONAS_DIR, SCRIPTS_DIR, OFFICE_DIR, SKILLS_DIR]:
    os.makedirs(d, exist_ok=True)

print(f"🔧 [Config] Workspace 路径已就绪: {WORKSPACE_DIR}")