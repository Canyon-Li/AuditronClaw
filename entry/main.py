import os
import sys
import time
import json
import asyncio
import random
from typing import Awaitable, Callable, List, Optional, Tuple
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.styles import Style
from prompt_toolkit.application import get_app

from auditronclaw.core.agent import create_agent_app
from auditronclaw.core.config import DB_PATH
from auditronclaw.core.session import (
    ApprovalRequest,
    SessionEngine,
    ToolCall,
    ToolResult,
    Reply,
    TurnEnd,
)
from auditronclaw.core.approval.gate import (
    ApprovalDecision,
    DecisionSource,
    REJECT_PHRASE,
    TurnOrigin,
)
from auditronclaw.core.approval.rules import ApprovalRule, RuleStore
from auditronclaw.core.bus import task_queue, TurnRequest
from auditronclaw.core.heartbeat import pacemaker_loop

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def type_line(text: str, delay: float = 0.008):
    for ch in text:
        print(ch, end='', flush=True)
        time.sleep(delay)
    print()

def print_banner():
    clear_screen()

    CYAN = '\033[38;5;51m'
    PURPLE = '\033[38;5;141m'
    SILVER = '\033[38;5;250m'
    DIM = '\033[2m'
    BOLD = '\033[1m'
    RESET = '\033[0m'
    WHITE = '\033[37m'

    logo = f"""{CYAN}{BOLD}
 █████╗ ██╗   ██╗██████╗ ██╗████████╗██████╗  ██████╗ ███╗   ██╗
██╔══██╗██║   ██╗██╔══██╗██║╚══██╔══╝██╔══██╗██╔═══██╗████╗  ██║
███████║██║   ██║██║  ██║██║   ██║   ██████╔╝██║   ██║██╔██╗ ██║
██╔══██║██║   ██║██║  ██║██║   ██║   ██╔══██╗██║   ██║██║╚██╗██║
██║  ██║╚██████╔╝██████╔╝██║   ██║   ██║  ██║╚██████╔╝██║ ╚████║
╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚═╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝

 ██████╗██╗      █████╗ ██╗    ██╗
██╔════╝██║     ██╔══██║██║    ██║
██║     ██║     ███████║██║ █╗ ██║
██║     ██║     ██╔══██║██║███╗██║
╚██████╗███████╗██║  ██║╚███╔███╔╝
 ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝
{RESET}"""

    sub_title = f"{WHITE}{BOLD} 👾 透明可审计的智能体终端 · {PURPLE}{BOLD}AuditronClaw{RESET}{WHITE}{BOLD}  {RESET}"

    quotes = [
        "Trust, but verify.",
        "Logs don't lie. Agents might.",
        "Every action leaves a trace.",
        "The sandbox remembers.",
        "Who audits the auditor?",
        "With great autonomy comes great logging.",
        "Injection attempted, injection rejected.",
        "Zero trust, full transparency.",
        "It's not a bug, it's an audit finding."
    ]
    quote = random.choice(quotes)
    meta = f" {SILVER}✦{RESET} {CYAN}{quote}{RESET}"

    tip = (
        f"{PURPLE} ✦ {RESET}"
        f"{SILVER}{PURPLE}{BOLD}AuditronClaw{RESET} 已完成启动。输入命令开始，输入 {PURPLE}/exit{RESET}{SILVER} 退出。{RESET}\n"
    )

    print(logo)
    print(sub_title)
    print() 
    time.sleep(0.12)
    print(meta)
    print() 
    type_line(tip, delay=0.004)


def cprint(text="", end="\n"):
    print_formatted_text(ANSI(str(text)), end=end)


# ============ 审批交互(04 票):回合内输入通路与输入冲突仲裁 ============
#
# 终端输入的单持有者是输入循环(user_input_loop);审批答案不另开终端通道,
# 由 ApprovalBridge 把两侧接起来:
# - worker 侧:引擎 interrupt 后调 bridge.responder(request),请求入桥挂起
# - 输入循环侧:每轮取下一条主提示前,先经 answer_pending_approvals 排空
#   挂起的审批(逐条问人、回填决定)
#
# 输入冲突的确定行为:主提示提交的行永远排队成下一回合(哪怕此刻审批正
# 挂起),审批答案只来自审批提示——两条通路互不吃对方的输入。审批请求块
# 由 handle_turn_event 在事件流里到达即打印(patch_stdout 护行),状态条以
# bridge.pending 提示"审批等待应答"。引擎超时掐死应答器时 future 即死,
# 死条目即时出桥(状态条同拍收回,不等操作员下次提交);输入循环退出时
# close() 把挂起审批一律按无人拒,回合必能收尾(单 worker 队列不被悬而
# 未决的审批挂死)。

_APPROVAL_ALIASES = {
    "y": (True, False), "yes": (True, False),
    "a": (True, True), "always": (True, True),
    "n": (False, False), "no": (False, False),
}

APPROVAL_OPTIONS = "[y] 确认一次 · [a] 永久允许 · [n] 拒绝"

# 死条目提示:引擎已超时(终局拒绝已发生),应答步不再问人
_STALE_APPROVAL_NOTICE = "  \033[38;5;242m该审批等待已超时失效(超时即终局拒绝),无需再答\033[0m"


def parse_approval_answer(text: str) -> Optional[ApprovalDecision]:
    """审批提示的选项解析(纯函数):y/a/n(容忍大小写、空白与全词)。

    无效输入返回 None——是"没听懂"不是"默认拒绝",由读循环重问,不猜。
    """
    approved_persist = _APPROVAL_ALIASES.get(text.strip().lower())
    if approved_persist is None:
        return None
    approved, persist = approved_persist
    source = DecisionSource.USER_PERSIST if persist else DecisionSource.USER_ONCE
    return ApprovalDecision(approved=approved, persist=persist, source=source)


def format_approval_block(request: ApprovalRequest) -> str:
    """审批提示块:完整参数 + 风险级 + 依据。

    操作员批的是具体动作,不是类别印象——命令行/路径/域名原样全量展示,
    多字节不转义(防审批滥用,spec「防审批滥用」)。
    """
    args_json = json.dumps(request.args, ensure_ascii=False, indent=2)
    indented = args_json.replace("\n", "\n    ")
    return (
        "  \033[33m⚠️  审批请求\033[0m "
        f"\033[38;5;250m(必批副作用 · 风险级 \033[0m\033[1m{request.risk_class}\033[0m\033[38;5;250m)\033[0m\n"
        f"  \033[38;5;250m工具:\033[0m {request.tool}\n"
        f"  \033[38;5;250m依据:\033[0m {request.reason}\n"
        f"  \033[38;5;250m参数:\033[0m {indented}"
    )


def format_decision_echo(decision: ApprovalDecision) -> str:
    """应答回显:操作员看得见自己的批复成了什么。"""
    if decision.approved and decision.persist:
        return "  \033[32m✓ 已批准并铸规则(永久允许)\033[0m"
    if decision.approved:
        return "  \033[32m✓ 已批准(仅本次)\033[0m"
    return "  \033[31m✗ 已拒绝\033[0m"


def approval_prompt_message(request: ApprovalRequest) -> str:
    """审批提示消息:工具名 + 三选项(读循环重问时消息不变)。"""
    return (f"  \033[33m⏸ 批准 {request.tool}?\033[0m "
            f"\033[38;5;250m{APPROVAL_OPTIONS}\033[0m : ")


async def prompt_approval_decision(
        request: ApprovalRequest,
        prompt: Callable[[str], Awaitable[str]]) -> ApprovalDecision:
    """读一个审批答案:无效输入重问;Ctrl+C / Ctrl+D 一律拒(fail-closed,
    一次误触不中断整个回合)。prompt(message) -> 文本,鸭子型注入。"""
    while True:
        try:
            text = await prompt(approval_prompt_message(request))
        except (KeyboardInterrupt, EOFError):
            return ApprovalDecision(approved=False, persist=False,
                                    source=DecisionSource.USER_ONCE)
        decision = parse_approval_answer(text)
        if decision is not None:
            return decision
        cprint("  \033[38;5;242m无效选项,请答 y / a / n\033[0m")


class ApprovalBridge:
    """审批应答桥:worker 侧应答器协程 ↔ 输入循环侧应答步。

    桥不打印、不碰回合队列——只递送请求与决定(输入仲裁规则见本节开头)。
    """

    def __init__(self):
        self._entries: List[Tuple[ApprovalRequest, asyncio.Future]] = []

    @property
    def pending(self) -> bool:
        """是否有挂起未决的审批(状态条提示用)。死条目即时出桥,不滞留。"""
        return any(not fut.done() for _r, fut in self._entries)

    async def responder(self, request: ApprovalRequest) -> ApprovalDecision:
        """引擎应答通道:请求入桥,等输入循环回填决定。

        引擎超时掐死本协程时把 future 一并取消;future 一旦终局(回填或
        取消)条目即时出桥——状态条随下一次重绘就收回"审批等待应答",
        不等操作员下次提交(04 票真机发现:死条目滞留会让状态条谎报)。
        """
        fut = asyncio.get_running_loop().create_future()
        self._entries.append((request, fut))
        fut.add_done_callback(self._drop_done)
        try:
            return await fut
        except asyncio.CancelledError:
            fut.cancel()
            raise

    def _drop_done(self, _fut: asyncio.Future) -> None:
        """终局条目出桥(取消/回填都走到;drain 已取走的条目无妨)。"""
        self._entries = [(r, f) for r, f in self._entries if not f.done()]

    def drain(self) -> List[Tuple[ApprovalRequest, asyncio.Future]]:
        """取走全部活条目(应答步消费;至多一条——引擎逐个打断逐个应答)。"""
        entries, self._entries = self._entries, []
        return [(r, f) for r, f in entries if not f.done()]

    @staticmethod
    def resolve(fut: asyncio.Future, decision: ApprovalDecision) -> bool:
        """回填决定;条目已死(超时被取消)返回 False,答案弃置。"""
        if fut.done():
            return False
        try:
            fut.set_result(decision)
            return True
        except asyncio.InvalidStateError:
            return False

    def close(self) -> int:
        """输入循环退出时收尾:挂起审批一律按无人拒(操作员已离场)。

        回合必能收尾——单 worker 队列不被一条悬而未决的审批挂死。
        """
        denied = 0
        for _request, fut in self.drain():
            if ApprovalBridge.resolve(fut, ApprovalDecision(
                    approved=False, persist=False,
                    source=DecisionSource.UNATTENDED)):
                denied += 1
        return denied


async def drain_bridge_until(bridge: ApprovalBridge, await_done: asyncio.Future) -> int:
    """退出后的收尾循环:await_done 完成前,桥里迟到的挂起审批一律按无人拒。

    输入循环退出时回合可能仍在跑(队列里还有未消费的回合),审批在其后
    才入桥——已经没有应答的人了,不等审批超时,逐拍收尾直至 await_done
    (默认 5 分钟的超时兜底仍在,这里只是把退出从"最久等满超时"收紧到
    "下一拍即拒")。返回累计按无人拒的条数。
    """
    total = 0
    while not await_done.done():
        total += bridge.close()
        await asyncio.wait({await_done}, timeout=0.25)
    return total


async def answer_pending_approvals(
        bridge: ApprovalBridge,
        read_answer: Callable[[ApprovalRequest], Awaitable[ApprovalDecision]]
        ) -> None:
    """输入循环的应答步(取下一条主提示前必经):挂起的审批逐条问人。

    死条目(引擎已超时,终局拒绝已发生)跳过不问人;问人途中条目死了
    同样作废——读与条目赛跑,超时即收回审批提示,不等操作员(提示层
    不得比引擎活得久),答案弃置只提示失效。read_answer 由交互形态注入
    (终端/Web 各配各的读法)。
    """
    for request, fut in bridge.drain():
        if fut.done():
            cprint(_STALE_APPROVAL_NOTICE)
            continue
        read_task = asyncio.create_task(read_answer(request))
        await asyncio.wait({read_task, fut}, return_when=asyncio.FIRST_COMPLETED)
        if fut.done():
            # 引擎已终局(超时拒绝/退出收尾):提示作废,读到一半也收回
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass
            cprint(_STALE_APPROVAL_NOTICE)
            continue
        decision = read_task.result()
        if ApprovalBridge.resolve(fut, decision):
            cprint(format_decision_echo(decision))
        else:
            cprint(_STALE_APPROVAL_NOTICE)


# ============ 规则管理面:/rules 清单与 /revoke 撤销(02 票管理面的终端呈现) ============

def _short_id(rule_id: str) -> str:
    """清单展示用的短 id(前 8 位);撤销按前缀匹配,展示长度即够用。"""
    return rule_id[:8]


def format_rules_table(rules: List[ApprovalRule]) -> str:
    """规则清单:动作/作用域/出处/铸成时间逐条一行(管理面可查)。"""
    if not rules:
        return ("  📋 审批规则清单(0 条)—— 冷启动:尚无规则;"
                "每条生产规则的第一现场都是一次真实审批")
    lines = [f"  📋 审批规则清单({len(rules)} 条)"]
    for idx, rule in enumerate(rules, 1):
        lines.append(
            f"  {idx}. [{rule.action}] {rule.scope} · {rule.source}"
            f" · {rule.created_at} · id {_short_id(rule.id)}")
    lines.append("  (撤销:/revoke <id 前缀>)")
    return "\n".join(lines)


def match_rule_id_prefix(rules: List[ApprovalRule], prefix: str) -> List[ApprovalRule]:
    """按 id 前缀找规则(大小写归一):唯一/歧义/未命中三态由调用方裁决。"""
    needle = prefix.strip().lower()
    return [r for r in rules if r.id.lower().startswith(needle)]


def handle_operator_command(text: str, rule_store: RuleStore) -> bool:
    """操作员命令(规则管理面):/rules 清单、/revoke 撤销。

    就地消费返回 True(不入回合队列、不惊动 agent——管理面与 agent 策略
    分界,spec「心跳自身不入门」同款界定);非命令返回 False。
    """
    parts = text.split()
    command = parts[0].lower() if parts else ""
    if command == "/rules":
        cprint(format_rules_table(rule_store.list_rules()))
        cprint()
        return True
    if command == "/revoke":
        if len(parts) < 2 or not parts[1].strip():
            cprint("  \033[38;5;242m用法:/revoke <id 前缀>(先 /rules 看清单)\033[0m")
            cprint()
            return True
        matches = match_rule_id_prefix(rule_store.list_rules(), parts[1])
        if not matches:
            cprint(f"  \033[38;5;242m未找到 id 前缀 {parts[1]} 对应的规则\033[0m")
            cprint()
            return True
        if len(matches) > 1:
            cprint(f"  \033[38;5;242m前缀 {parts[1]} 歧义(命中 {len(matches)} 条),请补长前缀:\033[0m")
            cprint(format_rules_table(matches))
            cprint()
            return True
        rule = rule_store.revoke_rule(matches[0].id)
        cprint(f"  \033[31m✗ 已撤销规则 {_short_id(rule.id)}\033[0m "
               f"[{rule.action}] {rule.scope}(撤销即失效并留审计)")
        cprint()
        return True
    return False


async def process_user_line(text: str, queue: asyncio.Queue,
                            rule_store: RuleStore) -> bool:
    """主提示一行提交的归属裁决(输入冲突规则的另一半)。

    操作员命令就地消费;其余(含 /exit 控制令牌)排队成下一回合信封,
    返回 True = 输入循环该退出。主提示的行永不作为审批答案——审批答案
    只来自审批提示。
    """
    if handle_operator_command(text, rule_store):
        return False
    await queue.put(TurnRequest(text=text, origin=TurnOrigin.HUMAN))
    return text.lower() in ("/exit", "/quit")


def handle_turn_event(event, spinner):
    """回合事件 → TUI 行为映射(spinner 状态机与打印,消费 SessionEngine 事件流)。

    与旧 astream 手写解析逐分支等价(等价性由 tests/test_tui_adapter.py 钉住):
    tool_call→工具态+打印工具名;tool_result→回思考态,审批门拒绝原文
    照印(2026-08-27 真机发现:模型转述会复读 thread 历史里的旧话术,
    操作员须直读门对 agent 说了什么,不赌转述);final reply→停
    spinner+打印;非 final reply 不显示(保现状);turn_end→行距收尾。
    """
    if isinstance(event, ToolCall):
        spinner.is_tool_calling = True
        spinner.tool_msg = f"唤醒内置工具 : {event.name}..."
        cprint(f"  ●\033[38;5;51m Tool Call: \033[0m{event.name}")
        cprint('')
    elif isinstance(event, ApprovalRequest):
        # 审批打断:审批块到达即打印(完整参数+风险级,patch_stdout 护行);
        # spinner 退出工具态转等人——回合未收尾,is_spinning 不动
        spinner.is_tool_calling = False
        cprint(format_approval_block(event))
        cprint('')
    elif isinstance(event, ToolResult):
        spinner.is_tool_calling = False
        if event.result.startswith(f"❌ {REJECT_PHRASE}"):
            # 门的话术原文照印:agent 收到的拒绝理由与它的转述可以不一致
            # (真机两例:tool_result 如实,回复却复读历史旧话术)——拒绝
            # 是否如实呈现,操作员以此行为准,不赌模型转述
            cprint(f"  \033[31m{event.result}\033[0m")
            cprint('')
    elif isinstance(event, Reply):
        if event.final:
            spinner.is_spinning = False

            lines = event.content.strip().split('\n')
            if lines:
                formatted_out = f"  \033[38;5;141m❯\033[0m \033[38;5;250m{lines[0]}"
                for line in lines[1:]:
                    formatted_out += f"\n    {line}"
                formatted_out += "\033[0m"
                cprint(formatted_out)
    elif isinstance(event, TurnEnd):
        spinner.is_spinning = False
        cprint() # 空出舒适的行距


async def async_main(thread_id: str = "local_geek_master"):
    print_banner()

    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    load_dotenv(env_path)
    
    current_provider = os.getenv("DEFAULT_PROVIDER", "aliyun")
    current_model = os.getenv("DEFAULT_MODEL", "glm-5")

    async with AsyncSqliteSaver.from_conn_string(DB_PATH) as memory:
        app = create_agent_app(provider_name=current_provider, model_name=current_model, checkpointer=memory, thread_id=thread_id)
        # 审批交互(04 票):应答桥接进引擎——人来源回合规则未命中的高危
        # 调用经桥问人;心跳/缺省来源构造上不问人(03 票保证)。规则管理面
        # 与门共用同一规则文件(RuleStore 即时读盘,撤销当次生效)。
        bridge = ApprovalBridge()
        rule_store = RuleStore()
        engine = SessionEngine(app, thread_id, approval_responder=bridge.responder)

        class SpinnerState:
            action_words = [
                "Thinking...",              
                "Working...",               
                "Beep boop...",             
                "Eating bugs...",           
                "Charging battery...",      
                "Brewing coffee...",        
                "Blinking lights...",       
                "Polishing pixels...",      
                "Scanning matrix...",       
                "Warming up circuits...",   
                "Syncing data...",          
                "Pinging server..."         
            ]
            current_words = [] 
            is_spinning = False
            start_time = 0
            frames = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
            is_tool_calling = False 
            tool_msg = ""           

        spinner = SpinnerState()


        def get_bottom_toolbar():
            # 审批挂起优先于 spinner:操作员在主提示里也看得见该应答了
            if bridge.pending:
                return ANSI("  \033[33m⏸ 审批等待应答\033[0m \033[38;5;250m— 提交当前输入后应答 "
                            + APPROVAL_OPTIONS + "\033[0m")
            if not spinner.is_spinning:
                return ANSI("")
            
            elapsed = time.time() - spinner.start_time
            if spinner.is_tool_calling:
                display_msg = spinner.tool_msg
            else:
                idx_word = int(elapsed) % len(spinner.current_words)
                display_msg = f"👾 {spinner.current_words[idx_word]}"

            idx_frame = int(elapsed * 12) % len(spinner.frames)
            frame = spinner.frames[idx_frame]
            

            return ANSI(f"  \033[38;5;51m{frame}\033[0m \033[38;5;250m{display_msg}\033[0m \033[38;5;141m[{elapsed:.1f}s]\033[0m")

        prompt_message = ANSI("  \033[38;5;51m❯\033[0m ")
        placeholder_text = ANSI("\033[3m\033[38;5;242minput...\033[0m")

        async def agent_worker():
            while True:
                item = await task_queue.get()
                # 队列项:类型化 TurnRequest(用户=HUMAN/心跳=HEARTBEAT)或
                # 裸控制令牌(/exit)。裸串无来源声明→按无人值守(fail-closed)
                text = item.text if isinstance(item, TurnRequest) else item
                if text.lower() in ["/exit", "/quit"]:
                    task_queue.task_done()
                    break
                origin = item.origin if isinstance(item, TurnRequest) else TurnOrigin.UNATTENDED

                spinner.current_words = spinner.action_words.copy()
                random.shuffle(spinner.current_words)

                spinner.start_time = time.time()
                spinner.is_spinning = True
                spinner.is_tool_calling = False

                try:
                    async for event in engine.run_turn(text, origin=origin):
                        handle_turn_event(event, spinner)
                except Exception as e:
                    spinner.is_spinning = False
                    cprint(f"  \033[31m[ ⚠️ 引擎异常 : {e} ]\033[0m")
                    cprint() # 空出舒适的行距

                spinner.is_spinning = False
                task_queue.task_done()

        async def user_input_loop():
            custom_style = Style.from_dict({
                'bottom-toolbar': 'bg:default fg:default noreverse',
            })
            
            session = PromptSession(
                bottom_toolbar=get_bottom_toolbar,
                style=custom_style,
                erase_when_done=True,
                reserve_space_for_menu=0  
            )
            
            async def redraw_timer():
                while True:
                    if spinner.is_spinning or bridge.pending:
                        try:
                            get_app().invalidate()
                        except Exception:
                            pass
                    await asyncio.sleep(0.08)

            redraw_task = asyncio.create_task(redraw_timer())

            # 审批提示的读法:与主提示同一个 PromptSession,消息换成审批问句
            # (回合内输入通路——prompt_async 只在此处与应答步两用)
            async def read_answer(request: ApprovalRequest) -> ApprovalDecision:
                return await prompt_approval_decision(
                    request, prompt=lambda msg: session.prompt_async(ANSI(msg)))

            while True:
                try:
                    # 应答步先于主提示:挂起的审批在下一条主提示前问人
                    # (输入仲裁规则,见 ApprovalBridge 一节的注释)
                    await answer_pending_approvals(bridge, read_answer)
                    user_input = await session.prompt_async(prompt_message, placeholder=placeholder_text)

                    user_input = user_input.strip()
                    if not user_input:
                        continue


                    padded_bubble = f"  ❯ {user_input}    "
                    cprint(f"\033[48;2;38;38;38m\033[38;5;255m{padded_bubble}\033[0m\n")

                    # 主提示行的归属:操作员命令就地消费;其余排队成下一回合
                    # 信封(来源 HUMAN,可问人)
                    if await process_user_line(user_input, task_queue, rule_store):
                        cprint("  \033[38;5;141m✦ 记忆已固化，AuditronClaw 进入休眠。\033[0m")
                        break

                except (KeyboardInterrupt, EOFError):
                    cprint("\n  \033[38;5;141m✦ 强制中断，AuditronClaw 进入休眠。\033[0m")
                    await task_queue.put("/exit")
                    break

            redraw_task.cancel()

        with patch_stdout():
            worker = asyncio.create_task(agent_worker())
            heartbeat_worker = asyncio.create_task(pacemaker_loop(task_queue=task_queue, check_interval=10))
            await user_input_loop()
            # 输入循环已退出:join 期间持续收尾——回合若在其后才弹出审批,
            # 也按无人拒(不等审批超时,退出不被拖长)
            join_task = asyncio.create_task(task_queue.join())
            denied = await drain_bridge_until(bridge, join_task)
            if denied:
                cprint(f"  \033[38;5;242m输入循环已退出,{denied} 条挂起审批按无人值守拒绝\033[0m")
            worker.cancel()
            heartbeat_worker.cancel()

def main(thread_id: str = "local_geek_master"):
    asyncio.run(async_main(thread_id=thread_id))

if __name__ == "__main__":
    main()