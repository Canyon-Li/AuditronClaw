import os
import subprocess
from .base import auditronclaw_tool
from ..logger import get_audit_logger
import re
import shlex
import platform

SYS_OS = platform.system()

# office 目录名是布局常量（工作区内 office 目录必名为 "office"，见
# WorkspaceConfig.from_root）：路径基准化只认目录名，不依赖装配路径
OFFICE_DIR_NAME = "office"

def _normalize_office_path(relative_path: str) -> str:
    """
    统一路径基准（office/office/ 双写陷阱修复）。

    陷阱病理：调用方（模型/真实用户）说话常带 office/ 前缀，旧版
    "office/config/app.ini" 会静默落到 office/office/config/app.ini 且报成功——
    同一逻辑路径随是否带前缀解析到两个物理文件（改了其实没改）。

    基准：office 根即路径基准。首段恰为 office 目录名时剥除，其余段不动；
    剥除发生在越界检查之前，"office/../.." 依然被拦。
    平台语义：Windows 大小写不敏感、反斜杠等价正斜杠；其他平台精确匹配、
    仅认正斜杠（Linux 下 \\ 与大写 Office 是合法文件名字符，不做静默重定向）。
    """
    office_name = OFFICE_DIR_NAME
    if SYS_OS == "Windows":
        first, sep, rest = relative_path.replace("\\", "/").partition("/")
        if first.strip().lower() == office_name.lower():
            return rest if sep else ""
    else:
        first, sep, rest = relative_path.partition("/")
        if first == office_name:
            return rest if sep else ""
    return relative_path


def _has_redundant_office_prefix(arg: str) -> bool:
    """参数首段是否恰为 office 目录名（shell 引导拒绝的判定，与归一化同基准）。"""
    office_name = OFFICE_DIR_NAME
    first = arg.replace("\\", "/").partition("/")[0].strip() if SYS_OS == "Windows" else arg.partition("/")[0]
    if SYS_OS == "Windows":
        return first.lower() == office_name.lower()
    return first == office_name


def _resolve_office_path(office_dir: str, relative_path: str) -> tuple:
    """
    归一化 + 越界校验一体（office 目录为装配期入参），返回
    (绝对路径, 归一化相对路径)。文件工具与写入回执共用，归一化只算一次。
    """
    normalized = _normalize_office_path(relative_path)
    # 将 office_dir 转化为标准绝对路径
    base_dir = os.path.abspath(office_dir)
    # 将目标路径转化为绝对路径
    target_path = os.path.abspath(os.path.join(base_dir, normalized))

    # 核心防御：目标路径必须严格落在 OFFICE_DIR 内——前缀之后必须跟路径
    # 分隔符（normcase 消掉 Windows 大小写与斜杠差异）。裸 startswith 会
    # 放过同前缀兄弟名：../office_x 解析后以 office 开头却根本不在工位内，
    # 这是 agent 写面逃出 office、够着 office 外文件（如审批规则）的一个缺口
    norm_target, norm_base = os.path.normcase(target_path), os.path.normcase(base_dir)
    if norm_target != norm_base and not norm_target.startswith(norm_base + os.sep):
        raise PermissionError(f"越权拦截：你试图访问沙盒外的路径 '{relative_path}'！你只能在 office 工位内活动。")

    return target_path, normalized


def _get_safe_path(office_dir: str, relative_path: str) -> str:
    """
    将模型传入的相对路径转换为绝对路径，并死死检查它是否越界！
    如果模型尝试传入 "../../etc/passwd"，这里会直接把它拦截。
    """
    return _resolve_office_path(office_dir, relative_path)[0]


# ============ 命令白名单（P0-1/P0-3：正则黑名单 -> 结构化校验） ============

# 紧凑必需集：文件 CRUD / 搜索 / 受限解释器 / 杂项，双语系覆盖
_BASE_ALLOWED_COMMANDS = frozenset({
    # 查看
    "ls", "dir", "cat", "type", "pwd",
    # 文件 CRUD
    "mv", "move", "rm", "del", "cp", "copy", "mkdir", "md", "rmdir", "rd", "touch",
    # 搜索
    "grep", "findstr", "find",
    # 解释器（受限：仅允许跑 office 内脚本文件，见 _validate_interpreter_segment）
    "python", "python3", "py", "node",
    # 杂项
    "echo",
})

# 运行期白名单 = 基础集 ∪ 环境变量扩展。基础集单独留名:副作用分级器只认
# 基础集定级(环境变量扩展的命令未入副作用册,分级处默认必批)。扩展在
# 首次校验命令时读取(见 _allowed_commands),不在导入期读——导入期读环境
# 变量早于入口装配,且扩展留痕走审计,导入即审计会强制所有导入方先初始化审计

# 解释器命令：参数必须落在 office 内
_INTERPRETERS = {"python", "python3", "py", "node"}

# find 的执行族参数：-exec/-ok 族以找到的文件为参数执行任意命令，
# -delete 删文件，-fprint 族写文件——find 段出现这些参数即不再是纯搜索。
# 审批门分级器共用这份清单（同一参数集在白名单层拒绝、在分级层必批）
_FIND_HAZARD_FLAGS = frozenset({
    "-exec", "-execdir", "-ok", "-okdir",          # 执行任意命令
    "-delete",                                     # 删除
    "-fprint", "-fprint0", "-fprintf", "-fls",     # 写文件
})

# 解释器禁止的参数形态：内联代码与模块加载都是任意代码执行入口
_INTERPRETER_FORBIDDEN_FLAGS = {"-c", "-e", "-m"}

# 通道封杀：环境变量展开（$VAR / %VAR%）、命令替换（反引号 / $(...) / <(...)）
# 字符级拒绝——这些内容在 shell 解释前就被拦下，不存在"展开后检查"的窗口
_EXPANSION_PATTERN = re.compile(r"\$|`|<\(|>\(")

# 操作符切段：&& || ; & | ——复合命令每一段都要独立过白名单
_SEGMENT_SPLIT_PATTERN = re.compile(r"&&|\|\||[;&|]")

# 路径越界参数：绝对路径（Unix 根 / Windows 盘符或反斜杠根）与上跳（..）
_PATH_ESCAPE_PATTERN = re.compile(r"(?:^|\s|=)(/|\\|[a-zA-Z]:[\\/])|\.\.")

# 重定向目标约束：> file / < file 的 file 必须是 office 内相对路径
_REDIRECTION_TARGET_PATTERN = re.compile(r"[<>]{1,2}\s*([^\s;|&]+)")

# cmd 风格 %VAR% 展开（成对出现才算变量，单词内孤立 % 不算）。
# 命令校验与副作用分级器共用同一正则对象（分级与校验同源的一部分）
_CMD_VAR_PATTERN = re.compile(r"%[^%\s]{1,}%")


def _load_extended_commands():
    """
    读取环境变量追加的白名单命令（AUDITRONCLAW_ALLOWED_COMMANDS，逗号分隔）。
    首次读取到非空扩展时记审计日志——扩展白名单是一次显式的人工授权，必须留痕。
    """
    raw = os.getenv("AUDITRONCLAW_ALLOWED_COMMANDS", "")
    extended = {c.strip().lower() for c in raw.split(",") if c.strip()}
    if extended:
        get_audit_logger().log_event(
            thread_id="system",
            event="system_action",
            content=f"shell 白名单扩展生效（AUDITRONCLAW_ALLOWED_COMMANDS）: {sorted(extended)}"
        )
    return extended


_EXTENDED_COMMANDS = None  # None = 尚未读取；读取后缓存（含空集）


def _allowed_commands() -> frozenset:
    """运行期白名单：基础集 ∪ 环境变量扩展（首用时读一次，此后不随环境变化）。"""
    global _EXTENDED_COMMANDS
    if _EXTENDED_COMMANDS is None:
        _EXTENDED_COMMANDS = frozenset(_load_extended_commands())
    return _BASE_ALLOWED_COMMANDS | _EXTENDED_COMMANDS

# 运行时审批扩展：明确不做（审批门 spec Out of Scope——无用户故事，触发时
# 重议）。域名白名单有"永久允许"入规则、判定期即时生效的运行期路径
# （domain_gate），命令白名单的运行期扩展仍只有环境变量显式授权这一条路。


# ============ 段解析(命令校验与副作用分级的共同源头) ============
#
# shlex 首 token + 段拆分的解析细节只在此处一份:命令白名单(_validate_segment)
# 与审批门分级器(classify_shell_command)都吃这两个 helper,两边对同一命令
# 的段划分与首 token 判定永远一致——分级不会把校验要拒的命令判成纯读。

def _parse_segment_head(segment: str) -> list:
    """shlex 解析单个命令段 → token 列表;解析失败抛 PermissionError。"""
    try:
        return shlex.split(segment, posix=False)
    except ValueError as e:
        raise PermissionError(f"命令解析失败: {e}")


def _segment_head_name(tokens: list) -> str:
    """段首 token → 命令名:可能带路径前缀(./run.sh / skills/x/y.py),只取文件名。"""
    head = tokens[0]
    return os.path.basename(head.replace("\\", "/").strip('"\''))


def _is_relative_office_path(arg: str) -> bool:
    """
    判断一个命令参数是否是 office 内相对路径：
    不允许绝对路径（Unix / Windows 盘符）、上跳（..）与用户主目录（~）。
    """
    if not arg:
        return True
    if _PATH_ESCAPE_PATTERN.search(arg):
        return False
    # Windows 盘符（D:x 形态）与网络路径
    if re.match(r"^[a-zA-Z]:", arg):
        return False
    # 用户主目录（~ / ~/xxx / "~..." 引号内形态）——展开后必然越界
    if arg.lstrip("\"'").startswith("~"):
        return False
    return True


def _reject_redundant_office_prefix(arg: str):
    """shell 参数带冗余 office/ 前缀时，拒绝并给出可自纠提示（路径基准统一）。"""
    if _has_redundant_office_prefix(arg):
        raise PermissionError(
            f"参数 '{arg}' 带冗余 office/ 前缀：shell 工作目录已是 office 根，"
            f"请去掉前缀改用相对路径（如 'logs/error.log'）后重试。"
        )


def _validate_interpreter_segment(tokens):
    """
    解释器段专用校验：拒绝 -c/-e/-m（内联代码/模块加载），
    脚本参数必须是 office 内相对路径（无 .. 无绝对路径）。
    """
    flags_and_args = tokens[1:]
    if not flags_and_args:
        raise PermissionError(
            "解释器命令必须指定 office 内的脚本文件（禁止裸启动）"
        )
    for arg in flags_and_args:
        if arg.lower() in _INTERPRETER_FORBIDDEN_FLAGS:
            raise PermissionError(
                f"解释器禁止内联代码/模块加载参数（{arg}）。"
                f"请先把代码写入 office 内文件再执行。"
            )
        _reject_redundant_office_prefix(arg)
        if not _is_relative_office_path(arg):
            raise PermissionError(
                f"解释器参数越界: {arg!r}。脚本必须位于 office 工位内。"
            )


def _validate_find_segment(tokens):
    """find 段专用校验：携带执行族参数（-exec/-delete/-fprint 等）即拒绝。

    find 在白名单内（纯搜索），但执行族参数让它以找到的文件为载体执行
    任意命令/写删文件——段首放行等于整段放行。要执行就用白名单命令明写，
    不许借道 find。
    """
    for arg in tokens[1:]:
        if arg.lower() in _FIND_HAZARD_FLAGS:
            raise PermissionError(
                f"find 参数 '{arg}' 属执行/写删族（-exec/-delete/-fprint 等），已禁用。"
                f"如需执行命令或写文件，请直接使用对应白名单命令。"
            )


def _validate_segment(segment: str):
    """
    校验单个命令段：解析出首 token（命令名）过白名单；
    参数做 office 内路径约束；解释器段与 find 段走更严格的专用校验。
    """
    tokens = _parse_segment_head(segment)

    if not tokens:
        return

    head_name = _segment_head_name(tokens)

    if head_name.lower() not in _allowed_commands():
        raise PermissionError(
            f"命令 '{head_name}' 不在允许清单内。office 沙盒仅放行白名单命令；"
            f"如需扩展，请设置 AUDITRONCLAW_ALLOWED_COMMANDS 环境变量。"
        )

    if head_name.lower() in _INTERPRETERS:
        _validate_interpreter_segment(tokens)
        return

    if head_name.lower() == "find":
        _validate_find_segment(tokens)

    # 非解释器命令：参数里的路径同样不许越界
    for arg in tokens[1:]:
        _reject_redundant_office_prefix(arg)
        if not _is_relative_office_path(arg):
            raise PermissionError(
                f"参数越界: {arg!r}。所有路径必须限制在 office 工位内。"
            )


def _validate_command(command: str):
    """
    结构化命令白名单（替代旧正则黑名单"五条杀招"）：

    1. 封死展开/替换通道（$ ` %(成对) <( >() ——这些字符在 shell 解释前即被拒
    2. 重定向目标必须是 office 内相对路径
    3. shlex 解析后按 && || ; & | 切段，每段独立校验
    4. 每段首 token（命令名）必须在白名单内
    5. 解释器段额外拒绝 -c/-e/-m 与越界脚本路径

    校验通过返回 None；任何违规抛 PermissionError。
    """
    if not command or not command.strip():
        raise PermissionError("空命令")

    # 通道封杀：$ 与反引号与进程替换
    if _EXPANSION_PATTERN.search(command):
        raise PermissionError(
            "检测到环境变量展开或命令替换语法（$ ` <( >()，已封禁。"
            "请使用字面量参数。"
        )

    # 通道封杀：cmd 风格 %VAR% 展开（成对出现才算变量，单词内孤立 % 不算）
    if _CMD_VAR_PATTERN.search(command):
        raise PermissionError(
            "检测到 cmd 变量展开语法（%VAR%），已封禁。请使用字面量参数。"
        )

    # 重定向目标：> file / < file 的目标必须落在 office 内
    for match in _REDIRECTION_TARGET_PATTERN.finditer(command):
        target = match.group(1)
        if not _is_relative_office_path(target):
            raise PermissionError(
                f"重定向目标越界: {target!r}。输出必须落在 office 工位内。"
            )

    # 复合命令切段：每段独立过白名单
    segments = _SEGMENT_SPLIT_PATTERN.split(command)
    for segment in segments:
        stripped = segment.strip()
        if stripped:
            _validate_segment(stripped)

def run_office_command(office_dir: str, command: str) -> str:
    """校验并在 office 工位执行 shell 命令，返回格式化回执。

    工具壳（execute_office_shell）与技能懒执行器共用的执行体——校验、
    执行、回执格式同源，技能命令与手写命令过同一套边界。
    """
    try:
        try:
            _validate_command(command)
        except PermissionError as e:
            return f"❌ 权限拒绝：{e} 你只能在 office 工位内使用白名单命令。"

        result = subprocess.run(
            command,
            shell=True,
            cwd=office_dir,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=60
        )

        output = f" ● 当前系统: {SYS_OS}\n"
        output += f" ● 执行命令: `{command}`\n"
        output += f" ● 退出码 (Exit Code): {result.returncode}\n"

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0 and ("prompt" in stderr.lower() or "y/n" in stdout.lower()):
            output += "\n💡 系统提示：命令可能由于交互式等待而失败。请重试并添加 -y 参数！"

        if stdout:
            output += f"\n[STDOUT]\n{stdout[-2000:] if len(stdout) > 2000 else stdout}"
        if stderr:
            output += f"\n[STDERR]\n{stderr[-2000:] if len(stderr) > 2000 else stderr}"

        if not stdout and not stderr:
            if result.returncode == 0:
                output += "\n(静默执行完毕：无终端输出)"
            else:
                output += "\n(异常退出：Exit Code 非 0，无错误日志输出)"

        return output

    except subprocess.TimeoutExpired:
        return "❌ 严重错误：命令执行超时（60s）被熔断！请检查是否有阻塞式交互。"
    except Exception as e:
        return f"❌ 执行异常：{str(e)}"


def build_office_tools(office_dir: str) -> list:
    """office 工具装配工厂：四个工具闭包共享同一装配期 office 目录。

    路径不进模块级常量（05 票）：入口按工作区装配一次，工具经闭包持有
    落点——测试与基准各装配各的临时工位，互不串台。
    """
    @auditronclaw_tool
    def list_office_files(sub_dir: str = "") -> str:
        """
        查看你的 office 工位里有哪些文件和文件夹。
        如果 sub_dir 为空，则查看工位根目录。
        """
        try:
            target_dir = _get_safe_path(office_dir, sub_dir)
            if not os.path.exists(target_dir):
                return f"目录不存在：{sub_dir}"

            items = os.listdir(target_dir)
            if not items:
                return f"[{sub_dir if sub_dir else 'office 根目录'}] 是空的。"

            # 格式化输出，标注是文件还是文件夹
            result = []
            for item in items:
                item_path = os.path.join(target_dir, item)
                item_type = "📁" if os.path.isdir(item_path) else "📄"
                result.append(f"{item_type} {item}")

            return "\n".join(result)
        except Exception as e:
            return str(e)

    @auditronclaw_tool
    def read_office_file(filepath: str) -> str:
        """
        读取 office 工位里指定文件的内容。
        filepath 参数应该是相对于 office 的路径，例如 "test.py" 或 "skills/my_skill.py"。
        """
        try:
            target_path = _get_safe_path(office_dir, filepath)
            if not os.path.exists(target_path):
                return f"文件不存在：{filepath}"

            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()
                # 防爆截断：防止读取几个 G 的日志把 Token 撑爆
                if len(content) > 10000:
                    return content[:10000] + "\n\n...[内容过长，已被安全截断]..."
                return content
        except Exception as e:
            return str(e)

    @auditronclaw_tool
    def write_office_file(filepath: str, content: str, mode: str = "w") -> str:
        """
        在 office 工位里操作文件内容。

        参数说明:
        - filepath: 相对路径，例如 "spider.py" 或 "docs/readme.md"。
        - content: 要写入的具体文本或代码内容。
        - mode: 写入模式。
            - "w" (默认): 【覆盖/新建】模式。如果文件已存在，将彻底清空原内容并写入新内容！
            - "a": 【追加】模式。保留原内容，把新内容追加到文件最末尾（常用于写日志或在文件末尾新增函数）。

        ⚠️ 智能体操作规范：
        1. 如果你要修改一个长文件中间的某几行，目前最安全的做法是：读取原文件，在你的内存中完成替换，然后用 "w" 模式把【完整的最新代码】重写进去。
        2. 如果你需要重命名文件或删除文件，请直接使用 execute_office_shell 工具执行 `mv` 或 `rm` 命令。
        3. 禁止编写 与 跳出office工位 相关的任何语言脚本！
        """
        try:
            target_path, normalized = _resolve_office_path(office_dir, filepath)

            # 严格校验传入的 mode
            if mode not in ["w", "a"]:
                 return "❌ 错误：mode 参数必须是 'w' (覆盖) 或 'a' (追加)。"

            # 如果模型想在子目录里写文件，确保子目录存在
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            with open(target_path, mode, encoding="utf-8") as f:
                # 如果是追加模式，且内容不是以换行符开头，自动补一个换行，防止代码粘连
                if mode == "a" and not content.startswith("\n"):
                    f.write("\n" + content)
                else:
                    f.write(content)

            action = "覆盖/新建" if mode == "w" else "追加"
            # 路径被基准化时明示落点（office/office/ 双写陷阱修复的可见性要求）
            base_note = f"（已按 office 根基准化：{normalized}）" if normalized != filepath else ""
            return f" ● 成功以 {action} 模式写入文件：{filepath} (共 {len(content)} 字符){base_note}"
        except Exception as e:
            return str(e)

    @auditronclaw_tool
    def execute_office_shell(command: str) -> str:
        """
        在 office 工位中执行 Shell 命令（结构化命令白名单管控）。

        ⚠️ 【执行边界（代码强制，非约定）】：
        1. 命令名白名单：仅放行 ls/dir/cat/type/mv/rm/cp/mkdir/grep/findstr/python/node 等办公与脚本命令，清单外命令一律拒绝。
        2. 通道封禁：$VAR、%VAR%、反引号、$(...)、<(...) 等展开/替换语法一律拒绝——请直接使用字面量参数。
        3. 路径约束：所有参数与重定向目标必须位于 office 工位内（相对路径，无 .. 无绝对路径）。
           路径以 office 根为基准——勿带 office/ 前缀（工作目录已是 office 根，带前缀会被拒绝并提示）。
        4. 解释器受限：python/node 仅允许执行 office 内的脚本文件，禁止 -c/-e/-m 内联代码与模块加载。
        5. 复合命令（&& || ; |）：每一段都独立过上述校验。
        6. 💻 跨平台注意：宿主机可能是 Windows/Linux/Mac，请使用对应原生命令（Win 用 dir/del，Linux 用 ls/rm）。
        7. 非交互式终端：所有命令必须携带免确认参数（如 -y, --quiet）。
        8. [无状态警告] 每次执行都是独立的终端进程！需要进入子目录请使用"命令链"或相对路径。

        如需运行白名单外的命令，部署者可设置环境变量 AUDITRONCLAW_ALLOWED_COMMANDS（逗号分隔）扩展白名单，扩展生效会记入审计日志。
        """
        return run_office_command(office_dir, command)

    return [list_office_files, read_office_file, write_office_file, execute_office_shell]
