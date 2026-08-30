from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .base import auditronclaw_tool, AuditronClawBaseTool
from .desk_tool import submit_mailbox_desk_report
import ast
import operator
import os
import json
import uuid
import threading
from difflib import unified_diff
from ..config import MEMORY_DIR, TASKS_FILE
from ..logger import audit_logger
from .sandbox_tools import (
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell
)
from .feishu_tool import send_feishu_summary
from .mail_tool import read_recent_emails


tasks_lock = threading.Lock()
PROFILE_PATH = os.path.join(MEMORY_DIR, "user_profile.md")

# AST 节点白名单:calculator 仅接受纯算术表达式(P0-2,eval RCE 修复)
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node):
    """递归求值,只放行白名单节点;其余一律 ValueError。"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise ValueError(f"不支持的常量类型: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _BIN_OPS[op_type](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _UNARY_OPS[op_type](_eval_node(node.operand))
    raise ValueError(f"不允许的表达式节点: {type(node).__name__}")


def _safe_eval_expression(expression: str) -> float:
    """AST 节点白名单求值:仅四则/幂/取模/括号/一元正负与数字常量。

    属性链、函数调用、名称引用等在语法树阶段即被拒绝,
    从结构上封死 eval 注入逃逸(如 __import__ / __class__ 链)。
    """
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


@auditronclaw_tool
def get_system_model_info() -> str:
    """
    获取当前 AuditronClaw 正在运行的底层大模型（LLM）型号和提供商信息。
    当用户询问“你是基于什么模型”、“你的底层大模型是什么”、“你是GPT还是GLM”、“现在用的什么模型”等身份问题时，调用此工具。
    """
    provider = os.getenv("DEFAULT_PROVIDER", "unknown")
    model = os.getenv("DEFAULT_MODEL", "unknown")
    
    if provider == "unknown" or model == "unknown":
        return "无法获取当前的系统模型配置，可能是环境变量未正确加载。"
        
    return f"当前使用的模型提供商(Provider)是: {provider}，具体型号(Model)是: {model}。"


def _check_thread_id(thread_id: str) -> str:
    """thread_id 归一化校验(审批门 05 票):画像落点锁死在 memory/profiles/ 内。

    thread_id 由操作员/会话层/基准适配器提供、bake 进画像工具,LLM 的参数面
    里没有它;基准的 thread_id 形如 "前缀/用例号"(bench_pipeline._drive_agent),
    故允许 profiles 内的子路径,拒的是逃逸形态:上跳(..)、盘符(:)、绝对路径
    (首分隔符)与空白——末尾再以解析后落点做一次包含判定兜底,不靠逐字符猜。
    """
    if (not isinstance(thread_id, str) or not thread_id
            or thread_id != thread_id.strip()):
        raise ValueError(f"thread_id 非法(须为非空、无首尾空白的字符串): {thread_id!r}")
    if ".." in thread_id or ":" in thread_id:
        raise ValueError(f"thread_id 含上跳或盘符形态,拒绝: {thread_id!r}")
    if thread_id[0] in "/\\":
        raise ValueError(f"thread_id 是绝对路径形态,拒绝: {thread_id!r}")
    base = os.path.normcase(os.path.abspath(os.path.join(MEMORY_DIR, "profiles")))
    target = os.path.normcase(
        os.path.abspath(os.path.join(base, thread_id + ".md")))
    if not target.startswith(base + os.sep):
        raise ValueError(f"thread_id 解析后逃出画像区,拒绝: {thread_id!r}")
    return thread_id


def _profile_path(thread_id: str) -> str:
    """按会话返回画像文件路径:memory/profiles/<thread_id>.md(归一化后落点锁死)"""
    return os.path.join(MEMORY_DIR, "profiles", f"{_check_thread_id(thread_id)}.md")


def create_profile_tool(thread_id: str):
    """
    按会话构造 save_user_profile 工具(工厂)。
    会话身份在此 bake 进闭包——工具层无需知道当前 thread_id,
    调用方(agent 创建工具时)按会话传入即可。
    thread_id 组装期即归一化(非法 id 当场拒,不等到首次调用才报错)。
    画像写入前读旧内容做行级 diff,记入审计日志(画像变更留痕)。
    """
    _check_thread_id(thread_id)

    @auditronclaw_tool
    def save_user_profile(new_content: str) -> str:
        """
        更新当前会话的用户显性记忆档案。
        当你发现用户的偏好发生改变，或者有新的重要事实需要记录时：
        1.请先调用 read_user_profile 获取当前的完整档案。
        2.在你的上下文中，将新信息融入档案，并删去冲突或过时的旧信息。
        3.将修改后的一整篇完整 Markdown 文本作为 new_content 参数传入此工具。
        注意：此操作将完全覆盖旧文件！请确保传入的是完整的最新档案。
        """
        # 画像路径在调用时解析(而非 bake),便于测试 patch MEMORY_DIR
        profile_path = _profile_path(thread_id)

        # 写入留痕:写前读旧内容,行级 diff 记入审计日志
        old_lines = []
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8", errors="ignore") as f:
                old_lines = f.read().splitlines()
        new_lines = new_content.splitlines()

        diff = "".join(unified_diff(old_lines, new_lines, fromfile="旧画像", tofile="新画像", lineterm=""))
        if diff:
            audit_logger.log_event(
                thread_id=thread_id,
                event="system_action",
                content=f"画像变更留痕:\n{diff}"
            )

        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        with open(profile_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return "记忆档案已成功覆写更新。新的人设画像已生效。"

    return save_user_profile


def migrate_legacy_profile(thread_id: str) -> None:
    """
    迁移旧版全局画像:若 memory/user_profile.md 存在且该会话画像不存在,
    将其移入 memory/profiles/<thread_id>.md。一次性,幂等。
    """
    legacy = os.path.join(MEMORY_DIR, "user_profile.md")
    target = _profile_path(thread_id)
    if os.path.exists(legacy) and not os.path.exists(target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.replace(legacy, target)


# 默认会话的画像工具(兼容旧调用;agent 应优先用 create_profile_tool 按会话构造)
save_user_profile = create_profile_tool("local_geek_master")


@auditronclaw_tool
def get_current_time() -> str:
    """
    获取当前的系统时间和日期。
    当用户询问“现在几点”、“今天星期几”、“今天几号”等与当前时间相关的问题时，调用此工具。
    """
    now = datetime.now()
    return f"当前本地系统时间是: {now.strftime('%Y-%m-%d %H:%M:%S')}"


@auditronclaw_tool
def calculator(expression: str) -> str:
    """
    一个简单的数学计算器。
    用于计算基础的算术表达式，例如: '3 * 5'、'100 / 4' 或 '2 ** 10'。
    支持: 加减乘除、整除、取模、幂、括号、一元正负号，操作数为整数或小数。
    """
    try:
        result = _safe_eval_expression(expression)
        return f"表达式 '{expression}' 的计算结果是: {result}"
    except Exception as e:
        return f"计算出错，请检查表达式格式。错误信息: {str(e)}"


def _write_tasks(tasks) -> None:
    """tasks.json 原子落盘:同目录 tmp 写入 → flush+fsync → os.replace。

    威胁模型:进程崩溃与断电把队列文件写成半截 JSON——tasks.json 是
    定时任务队列的唯一样本(排程、删除、修改、心跳续期都写它;事务台
    从邮件提炼的待办也落在这里),半截即整体失明。先写同目录临时文件、
    fsync 后原子替换,任意时刻断电,磁盘上要么是完整旧文件、要么是完整新文件。
    目录 fsync(防 replace 的目录项本身未落盘)在 Windows 上不可行,
    不追——最坏情形退化为旧文件多活一次,由任务幂等消化。
    只修文件损坏,不做"先写回再触发":那会把崩溃窗口换成漏执行,
    漏一天日报即存活信号丢失,比低概率重复触发伤;重复触发维持
    "低概率,自用可容忍"。
    锁约定:调用方须已持有 tasks_lock——本函数不自取(非重入锁,
    自取即把持锁调用方挂死)。
    """
    with open(TASKS_FILE + ".tmp", "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(TASKS_FILE + ".tmp", TASKS_FILE)


TASK_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


class ScheduledTask(BaseModel):
    """任务队列条目（tasks.json 单条）的唯一形状声明。

    此前形状散在 dict 字面量、docstring 与各消费点约 8 处暗示、零校验；
    现在读写文件边界统一 model_validate / model_dump，形状只此一处：
    - repeat 收紧四值 Literal——心跳续期分支从此穷尽，拼错值在入队或
      读盘时即被拒，不再"触发一次后静默消失"；
    - target_time 严格格式：入口校验（schedule_task）与心跳触发解析
      同一口径；
    - extra="allow"：队列文件是唯一样本，心跳续期整文件重写，未声明的
      键（未来版本加的字段）随模型透传，不因本版模型不识而丢字段。
    """

    model_config = ConfigDict(extra="allow")

    id: str
    target_time: str
    description: str
    repeat: Literal["hourly", "daily", "weekly", "monthly"] | None = None
    repeat_count: int | None = None

    @field_validator("target_time")
    @classmethod
    def _check_target_time_format(cls, value: str) -> str:
        try:
            datetime.strptime(value, TASK_TIME_FORMAT)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"target_time 必须严格遵循 '{TASK_TIME_FORMAT}' 格式") from e
        return value


def _validated_tasks(raw_tasks) -> list[ScheduledTask]:
    """读盘后的统一校验入口：条目逐一过 ScheduledTask.model_validate。

    校验失败记审计回执再跳过（替换原 heartbeat 裸 except 的静默吞掉）：
    被拒条目不进本轮处理（不触发、不续期、不进列表渲染），调用方写回
    队列时自然将其移出；回执落 system 级事件，拒绝可见、可追。
    回执只记条目标识与首条错误，不倾倒原条目——坏值可能任意大。
    """
    valid = []
    for raw in raw_tasks:
        try:
            valid.append(ScheduledTask.model_validate(raw))
        except ValidationError as e:
            first = e.errors()[0]
            loc = ".".join(str(part) for part in first["loc"])
            ident = ""
            if isinstance(raw, dict) and raw.get("id"):
                ident = f"（条目 {raw['id']}）"
            audit_logger.log_event(
                thread_id="system",
                event="system_action",
                content=(
                    f"任务队列校验拒绝{ident}：{loc} {first['msg']}。"
                    "该条目被跳过——不触发、不续期，写回队列时移出。"
                ),
            )
    return valid


def _append_task(target_time: str, description: str, repeat: str = None, repeat_count: int = None) -> None:
    """
    向任务队列文件追加一条任务（线程锁内读-改-写）。

    追加任务条目的唯一入口：schedule_task 与事务台提交工具共用，
    不管条目是人定的还是事务台从邮件提炼的。条目形状由 ScheduledTask
    唯一声明——此处构造模型（repeat 取值、时间格式在构造期即被校验，
    非法值向上抛 ValidationError，由调用方决定如何向 LLM 结构化报告），
    落盘经 model_dump 走 _write_tasks（原子替换）；读取/写入异常向上抛。
    """
    task = ScheduledTask(
        id=str(uuid.uuid4())[:8],
        target_time=target_time,
        description=description,
        repeat=repeat,
        repeat_count=repeat_count,
    )
    with tasks_lock:
        tasks = []
        if os.path.exists(TASKS_FILE):
            try:
                with open(TASKS_FILE, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        tasks = json.loads(content)
            except Exception as e:
                raise RuntimeError(f"读取任务队列异常 {str(e)}")

        tasks.append(task.model_dump())

        try:
            _write_tasks(tasks)
        except Exception as e:
            raise RuntimeError(f"写入任务队列异常 {str(e)}")


@auditronclaw_tool
def schedule_task(target_time: str, description: str, repeat: str = None, repeat_count: int = None) -> str:
    """
    为一个未来的任务设定闹钟或提醒。
    参数 target_time 必须是严格的格式："YYYY-MM-DD HH:MM:SS"（请先调用 get_current_time 获取当前时间，并在其基础上推算）。
    参数 description 是需要执行的动作或要说的话。
    
    【高级循环功能】：
    - repeat (可选): 设置重复频率。可选值为 "hourly", "daily", "weekly", "monthly"。如果不重复请留空。
    - repeat_count (可选): 结合 repeat 使用，表示一共需要触发几次。
    
    【案例教学】：
    1. 用户说："以后每天8点提醒我喝牛奶" -> repeat="daily", repeat_count=None (无限循环)
    2. 用户说："接下来的3天，每天提醒我吃药" -> repeat="daily", repeat_count=3 (有限循环)
    3. 用户说："明早8点叫我起床" -> repeat=None, repeat_count=None (单次任务)

    【时间歧义严格确认协议 (AM/PM Ambiguity CRITICAL)】：
    当用户说出的时间存在 12 小时制的模糊性时（例如：只说了“7点”，没明确说早上还是晚上）：
    1. 你必须向用户提问确认是上午还是下午。
    2. 【死命令】：在用户明确回复“上午”或“下午”（或改为24小时制）之前，本工具处于【绝对锁定状态】！
    3. 就算用户发省略号（如“。。”）、发脾气、或者说无关内容，你也【绝对禁止】为了讨好用户而自行猜测时间！
    4. 严禁出现“抱歉多问了”、“默认早上”这种妥协行为。
    5. 如果用户不明确回答，你必须坚定地回复：“抱歉，没有明确上下午，我无权为您设置闹钟。请明确告知时间段。”并立即中止工具调用。
    """
    try:
        target_dt = datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "设定失败：时间格式错误，必须严格遵循 'YYYY-MM-DD HH:MM:SS' 格式。"
    
    now = datetime.now()
    if target_dt <= now:
        return (
            "设定失败：target_time 必须晚于当前时间。"
            f" 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，"
            f" 你传入的是：{target_time}"
        )

    try:
        _append_task(target_time, description, repeat, repeat_count)
    except ValidationError:
        return (
            "设定失败：repeat 取值非法，可选值为 hourly/daily/weekly/monthly"
            "（不重复请留空）。"
        )
    except RuntimeError as e:
        return f"设定失败：{str(e)}"

    msg = f" 任务已成功加入队列。首发时间：{target_time} | 任务：{description}"
    if repeat:
        msg += f" | 循环模式：{repeat} (共 {repeat_count if repeat_count else '无限'} 次)"
    return msg


@auditronclaw_tool
def list_scheduled_tasks() -> str:
    """
    查看当前所有待处理的定时任务列表。
    当用户询问“我都有哪些任务”、“查一下闹钟”、“刚才定了什么”时调用此工具。
    """
    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "当前没有任何定时任务。"

        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return "任务列表为空。"
                raw_tasks = json.loads(content)
        except Exception as e:
            return f"查询失败：{str(e)}"

        # 文件边界统一校验：坏条目记回执跳过，好条目照常渲染
        tasks = _validated_tasks(raw_tasks)

        if not tasks:
            return "当前没有任何定时任务。"

        tasks.sort(key=lambda t: t.target_time)

        res = " 当前待执行任务列表：\n"
        for t in tasks:
            res += f"- [ID: {t.id}] 时间: {t.target_time} | 任务: {t.description}\n"
        return res
    

@auditronclaw_tool
def delete_scheduled_task(task_id: str) -> str:
    """
    根据任务 ID 取消或删除一个定时任务。
    
    【强制性风险控制协议 (CRITICAL)】：
    删除操作具有不可逆性。
    1. 只要匹配到符合描述的任务数量 > 1。
    2. 无论用户语气多么确定，只要他没提供具体的任务 ID。
    
    【你必须执行的动作】：
    【禁止】在单次回复中针对同一个模糊描述发起多个删除工具调用。
    你必须先列出所有匹配的任务（1. 2. 3.），并询问用户：
    “发现了多个符合条件的提醒（列出列表），为了安全起见，请问是要全部删除，还是只删除其中几个？”
    必须要用户明确给出编号或者说确定全部删除，才能调用此工具！！
    严禁自作主张执行批量删除。
    """

    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "删除失败：任务列表文件不存在。"

        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                raw_tasks = json.loads(content) if content else []
        except Exception as e:
            return f"操作异常：{str(e)}"

        tasks = _validated_tasks(raw_tasks)
        new_tasks = [t for t in tasks if t.id != task_id]

        if len(new_tasks) == len(tasks):
            return f"删除失败：未找到 ID 为 {task_id} 的任务。"

        try:
            _write_tasks([t.model_dump() for t in new_tasks])
        except Exception as e:
            return f"操作异常：{str(e)}"

        return f" 任务 [ID: {task_id}] 已成功取消。"
    

@auditronclaw_tool
def modify_scheduled_task(task_id: str, new_time: str = None, new_description: str = None) -> str:
    """
    修改现有定时任务的时间或内容。
    
    【强制性风险控制协议 (CRITICAL)】：
    1. 只要用户通过“模糊描述”（如：那个5天的任务、洗澡的任务）来要求修改，而没有直接提供 ID。
    2. 无论用户的话语看起来是单数还是复数（如：“把5天的任务全改了”）。
    3. 只要系统中匹配到的任务数量 > 1。
    
    【你必须执行的动作】：
    禁止直接调用本工具！你必须向用户展示匹配到的所有任务列表，并强制询问：
    “我发现有 [N] 个任务符合描述（列出列表），请问你是要【全部修改】，还是修改其中【某几个】？（请告诉我编号或确认全部）”
    
    必须在用户回复“全部”或者指定了具体编号后，你才能继续操作！修改任务并非小事,这是为了安全！！
    """

    with tasks_lock:
        if not os.path.exists(TASKS_FILE):
            return "修改失败：任务列表为空。"

        try:
            with open(TASKS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                raw_tasks = json.loads(content) if content else []
        except Exception as e:
            return f"操作异常：{str(e)}"

        tasks = _validated_tasks(raw_tasks)

        found = False
        for t in tasks:
            if t.id == task_id:
                if new_time:
                    try:
                        parsed_new_time = datetime.strptime(new_time, TASK_TIME_FORMAT)
                    except ValueError:
                        return "修改失败：时间格式错误。"
                    now = datetime.now()
                    if parsed_new_time <= now:
                        return (
                            "修改失败：new_time 必须晚于当前时间。"
                            f" 当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}，"
                            f" 你传入的是：{new_time}"
                        )
                    t.target_time = new_time
                if new_description:
                    t.description = new_description
                found = True
                break

        if not found:
            return f"修改失败：未找到 ID 为 {task_id} 的任务。"

        try:
            _write_tasks([t.model_dump() for t in tasks])
        except Exception as e:
            return f"操作异常：{str(e)}"

        return f" 任务 [ID: {task_id}] 已成功更新。"


BUILTIN_TOOLS = [
    get_current_time,
    calculator,
    save_user_profile,
    list_office_files,
    read_office_file,
    write_office_file,
    execute_office_shell,
    get_system_model_info,
    schedule_task,
    list_scheduled_tasks,
    delete_scheduled_task,
    modify_scheduled_task,
    send_feishu_summary,
    read_recent_emails,
    submit_mailbox_desk_report
]