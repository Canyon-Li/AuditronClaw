import os
import typer
import questionary
import logging
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from dotenv import set_key, load_dotenv, unset_key, dotenv_values
import sys

from auditronclaw.core.provider import get_provider
from langchain_core.messages import HumanMessage

ENTRY_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(ENTRY_DIR) 

os.chdir(PROJECT_ROOT)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

app = typer.Typer(help="AuditronClaw - 透明可审计的智能体终端")
console = Console()

ui_style = questionary.Style([
    ('qmark', 'fg:#8d52ff bold'),       
    ('question', 'fg:#00ffff bold'),    
    ('answer', 'fg:#8d52ff bold'),      
    ('pointer', 'fg:#00ffff bold'),     
    ('highlighted', 'fg:#00ffff bold'), 
    ('selected', 'fg:#00ffff'),
    ('instruction', 'fg:#808080 dim'),  
])

ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

@app.command("config")
def config_wizard():
    console.clear()
    console.print(Panel(
        "👾 Welcome to [bold #8d52ff]AuditronClaw[/bold #8d52ff]...\n\n☁️[dim] 请完成模型配置，我们将把密钥安全固化在本地。[/dim]", 
        title="[bold white]✦  AuditronClaw Config[/bold white]", 
        border_style="#8d52ff"
    ))
    provider_raw = questionary.select(
        "选择你的模型提供商 (Provider):",
        choices=["openai", "anthropic", "aliyun (openai compatible)","tencent (openai compatible)", "z.ai (openai compatible)", "other (openai compatible)", "ollama"],
        style=ui_style,
        instruction="(按上下键选择，回车确认)"
    ).ask()

    if not provider_raw:
        console.print("[dim #8d52ff]✦   录入中断，AuditronClaw 配置已取消。[/dim #8d52ff]")
        return

    provider = provider_raw.split(" ")[0].strip()
    is_openai_compatible = "openai" in provider_raw.lower()

    model_name = questionary.text(
        "输入指定的模型型号 (如 gpt-4o-mini, qwen-max, glm-4 等):",
        style=ui_style
    ).ask()

    if model_name is None:
        console.print("[dim #8d52ff]✦   录入中断，AuditronClaw 配置已取消。[/dim #8d52ff]")
        return

    api_key = ""
    env_key = ""
    if provider != "ollama":
        if is_openai_compatible:
            env_key = "OPENAI_API_KEY"
        elif provider == "anthropic":
            env_key = "ANTHROPIC_API_KEY"

        api_key = questionary.password(
            f"输入你的 {env_key} (对应 {provider_raw}):",
            style=ui_style
        ).ask()

        if api_key is None:
            console.print("[dim #8d52ff]✦   录入中断，AuditronClaw 配置已取消。[/dim #8d52ff]")
            return

    base_url = ""
    if provider in ["openai", "anthropic"]:
        base_url = questionary.text(
            f"输入 {provider} 代理 Base URL (直连请直接回车跳过):",
            style=ui_style
        ).ask()
    elif provider == "ollama":
        base_url = questionary.text(
            "输入 Ollama Base URL (默认 http://localhost:11434，直接回车跳过):",
            style=ui_style
        ).ask()
    else:
        base_url = questionary.text(
            "输入兼容 Base URL (不填直接回车将使用官方默认地址):",
            style=ui_style
        ).ask()

    if base_url is None:
        console.print("[dim #8d52ff]✦   录入中断，AuditronClaw 配置已取消。[/dim #8d52ff]")
        return

    console.print("\n[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]")

    with Status(f"[bold #8d52ff]正在连接 {provider.upper()} 引擎并发送探测包...[/bold #8d52ff]", spinner="dots", spinner_style="#00ffff"):
        try:
            if env_key and api_key:
                os.environ[env_key] = api_key
            if base_url:
                if is_openai_compatible:
                    os.environ["OPENAI_API_BASE"] = base_url
                else:
                    os.environ[f"{provider.upper()}_BASE_URL"] = base_url

            llm = get_provider(provider_name=provider, model_name=model_name)
            llm.invoke([HumanMessage(content="回复我'收到'。")])  # 连通性探测:能 invoke 即成功

            console.print(" [bold #00ffff][ 配置成功!][/bold #00ffff]")
            
        except Exception as e:

            console.print(f" [bold #8d52ff][ 配置失败!][/bold #8d52ff]  无法连接到模型，请检查 Key、Base URL、模型型号 或 网络！\n[dim]错误信息: {str(e)}[/dim]")
            return


    if not os.path.exists(ENV_PATH):
        open(ENV_PATH, 'w').close()

    logging.getLogger("dotenv.main").setLevel(logging.ERROR)

    unset_key(ENV_PATH, "OPENAI_API_BASE")
    unset_key(ENV_PATH, "ANTHROPIC_BASE_URL")
    unset_key(ENV_PATH, "OLLAMA_BASE_URL")

    if env_key and api_key:
        set_key(ENV_PATH, env_key, api_key)
        
    if base_url:
        if is_openai_compatible:
            set_key(ENV_PATH, "OPENAI_API_BASE", base_url)
        else:
            set_key(ENV_PATH, f"{provider.upper()}_BASE_URL", base_url)
    
    set_key(ENV_PATH, "DEFAULT_PROVIDER", provider)
    set_key(ENV_PATH, "DEFAULT_MODEL", model_name)

    # 工作区落点首写(05 票):from_env 无默认回退,向导替用户把检出位置写明;
    # 已显式配置过的值不覆盖——运行者自定义的落点优先于向导默认
    if "AUDITRONCLAW_WORKSPACE" not in dotenv_values(ENV_PATH):
        set_key(ENV_PATH, "AUDITRONCLAW_WORKSPACE",
                os.path.join(PROJECT_ROOT, "workspace"))

    console.print(Panel(
        f"配置已保存至 [#8d52ff]{ENV_PATH}[/#8d52ff]\n"
        f"当前默认提供商: [#8d52ff]{provider}[/#8d52ff] | 模型: [#8d52ff]{model_name}[/#8d52ff]\n\n"
        f"👉 输入 [bold #00ffff]auditronclaw run[/bold #00ffff] 即可启动系统！",
        border_style="#00ffff"
    ))

def _show_boot_error():
    console.print(Panel(
        "[bold #00ffff]AuditronClaw未完成配置![/bold #00ffff]\n\n"
        "[#8d52ff]检测到 API Key、模型或Baseurl。请重新执行以下命令完成配置：[/#8d52ff]\n"
        "[bold #00ffff]auditronclaw config[/bold #00ffff]",
        title="[bold #8d52ff]⚠️ Boot Sequence Failed[/bold #8d52ff]",
        border_style="#8d52ff"
    ))


def _boot_env_ready() -> bool:
    """启动自检(load_dotenv 后调用):提供商/型号齐备,按提供商核 API Key。"""
    provider = os.getenv("DEFAULT_PROVIDER")
    model = os.getenv("DEFAULT_MODEL")
    if not provider or not model:
        return False
    if provider != "ollama":
        if provider in ["openai", "aliyun", "z.ai", "tencent", "other"]:
            return bool(os.getenv("OPENAI_API_KEY"))
        if provider == "anthropic":
            return bool(os.getenv("ANTHROPIC_API_KEY"))
    return True


@app.command("run")
def run_agent(thread: str = typer.Option("local_geek_master", "--thread", help="会话标识,独立的历史/日志/画像。默认 local_geek_master 兼容现有数据。")):
    load_dotenv(ENV_PATH)
    if not _boot_env_ready():
        _show_boot_error()
        raise typer.Exit()

    import entry.main as auditronclaw_main
    auditronclaw_main.main(thread_id=thread)

@app.command("web")
def run_web(
    port: int = typer.Option(8642, "--port", help="Web 终端监听端口,仅绑定 127.0.0.1。"),
    thread: str = typer.Option("local_geek_master", "--thread", help="会话标识,引擎按它隔离历史/日志/画像。"),
):
    """启动 Web 终端:本进程成为唯一属主(引擎/队列/心跳进程内运行)。"""
    import uvicorn

    from auditronclaw.core.config import WorkspaceConfig
    from auditronclaw.core.logger import init_audit_logger
    from entry.web import create_web_app, generate_token
    from entry.web_owner import assemble_backend_owner

    load_dotenv(ENV_PATH)
    if not _boot_env_ready():
        _show_boot_error()
        raise typer.Exit()
    provider = os.getenv("DEFAULT_PROVIDER")
    model = os.getenv("DEFAULT_MODEL")

    cfg = WorkspaceConfig.from_env()
    cfg.ensure_dirs()
    init_audit_logger(cfg.log_dir)

    token = generate_token()
    console.print(Panel(
        "👾 [bold #8d52ff]AuditronClaw Web 终端[/bold #8d52ff] 已启动(仅本机访问)\n\n"
        f"会话 [bold #00ffff]{thread}[/bold #00ffff] 由本进程唯一属主驱动——引擎、队列与心跳随服务启动运行。\n"
        "[bold red]⚠ 双属主禁令:TUI 与本服务不可同时驱动同一会话。[/bold red]\n\n"
        "[dim]token 每次启动随机生成,无 token 或错 token 的请求一律 403。[/dim]",
        title="[bold white]✦  Web Terminal[/bold white]",
        border_style="#8d52ff"
    ))
    # URL 单独成行打印:面板内 80 列会折行,拼不出完整可点链接
    console.print(f"👉 [bold #00ffff]http://127.0.0.1:{port}/?token={token}[/bold #00ffff]\n")
    owner_factory = assemble_backend_owner(
        thread_id=thread, provider_name=provider, model_name=model, workspace=cfg)
    uvicorn.run(create_web_app(token=token, owner_factory=owner_factory),
                host="127.0.0.1", port=port)

@app.command("monitor")
def run_monitor(thread: str = typer.Option("local_geek_master", "--thread", help="要监听的会话标识,对应 <thread>.jsonl 日志。")):
    try:
        import entry.monitor as auditronclaw_monitor
        auditronclaw_monitor.main(thread_id=thread)
    except ImportError as e:
        console.print(f"[bold red]启动失败：找不到监视器模块！[/bold red]\n[dim]请确保 monitor.py 和 cli.py 在同一目录下。\n报错信息: {e}[/dim]")

def main():
    app()

if __name__ == "__main__":
    main()