"""Web 终端后端:token 鉴权中间件 + web/dist 静态托管 + 属主 REST 端点。

本进程即唯一属主(引擎/队列/心跳在 lifespan 内装配,见 entry/web_owner);
浏览器是它的客户端。REST 承载静态资源、启动快照与审计流查询,实时
回合流走 WS(契约定稿见 entry/web_ws)。威胁模型是 localhost 绑定 +
token——堵恶意网页对 127.0.0.1 的 CSRF/DNS rebinding 替点审批;token
由启动入口随机生成并打印,REST 与 WS 握手走同一中间件校验。
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import asdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from entry.web_ws import register_ws_route

if TYPE_CHECKING:
    from entry.web_owner import BackendOwner

logger = logging.getLogger(__name__)

# 静态产物默认落点:web 前端模块的构建产物(gitignore 不入库)
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "dist"

# 首连种下的 cookie 名:带 token 的 / 响应下发,浏览器拉取静态资产凭它过门
COOKIE_NAME = "auditronclaw_token"

_DIST_NOT_BUILT_HINT = "web/dist 未构建:先在 web/ 下执行 npm run build"
_NO_OWNER_HINT = "属主未装配(脚手架形态):引擎/队列/心跳未运行,无数据可查"


def generate_token() -> str:
    """生成启动期一次性随机 token,操作员经带 token 的 URL 首连。"""
    return secrets.token_urlsafe(32)


class TokenAuthMiddleware:
    """无 token 或错 token 一律拒:http 403,websocket 拒握手(close 1008)。

    纯 ASGI 形态同时覆盖 http 与 websocket 两类 scope,WS 路由接入时
    直接复用,不再另起校验层。token 取 URL query(浏览器 WS 握手无法
    自定义 header,统一以 query 首选)或首连种下的 cookie——页面 HTML
    引用的资产 URL 不携带凭据,浏览器资产请求只能靠 cookie 过门。
    比较恒时;cookie 带 HttpOnly + SameSite=Strict,跨站请求不携带。
    """

    def __init__(self, app, token: str):
        self.app = app
        self._expected = token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        provided, from_query = self._extract_token(scope)
        if provided is not None and secrets.compare_digest(provided, self._expected):
            if scope["type"] == "http" and from_query:
                send = self._with_cookie(send, provided)
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http":
            await JSONResponse({"detail": "missing or invalid token"}, status_code=403)(
                scope, receive, send
            )
        else:
            await send({"type": "websocket.close", "code": 1008})

    @staticmethod
    def _with_cookie(send, token: bytes):
        """在响应头上追加 Set-Cookie(仅 query 形态校验通过时调用)。"""

        async def wrapper(message):
            if message["type"] == "http.response.start":
                cookie = (
                    f"{COOKIE_NAME}={token.decode('utf-8')}; Path=/; "
                    "HttpOnly; SameSite=Strict"
                )
                headers = list(message.get("headers", []))
                headers.append((b"set-cookie", cookie.encode("utf-8")))
                message = {**message, "headers": headers}
            await send(message)

        return wrapper

    @staticmethod
    def _extract_token(scope) -> tuple[bytes | None, bool]:
        """返回 (token, 是否来自 query);query 优先于 cookie。"""
        for key, value in parse_qsl(scope.get("query_string", b"").decode("latin-1")):
            if key == "token" and value:
                return value.encode("utf-8"), True
        for name, value in scope.get("headers", []):
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    key, _, cookie_value = part.strip().partition("=")
                    if key == COOKIE_NAME and cookie_value:
                        return cookie_value.encode("utf-8"), False
        return None, False


def create_web_app(token: str, static_dir: Path | str | None = None,
                   owner_factory: Optional[Callable[[], Awaitable["BackendOwner"]]]
                   = None) -> FastAPI:
    """Web 终端 app 工厂:token 鉴权中间件 + 属主端点 + web/dist 静态托管。

    owner_factory 注入即在 lifespan 内装配唯一属主(引擎/队列/心跳,
    见 entry.web_owner);不注入为脚手架形态,属主端点明确 503。
    static_dir 注入供测试,缺省锚仓库 web/dist。dist 未构建时不炸启动
    (带 token 访问得 503 提示先构建)——后端行为不依赖 Node 存在。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owner: BackendOwner | None = None
        if owner_factory is not None:
            owner = await owner_factory()
            await owner.start()
            app.state.owner = owner
        try:
            yield
        finally:
            if owner is not None:
                await owner.stop()

    app = FastAPI(
        title="AuditronClaw Web 终端",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TokenAuthMiddleware, token=token)
    _register_owner_routes(app)
    register_ws_route(app)

    directory = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    if directory.is_dir():
        # API 路由先于 "/" 挂载注册,静态兜底不影响 /api/* 与 /ws 命中
        app.mount("/", StaticFiles(directory=directory, html=True), name="web")
        return app

    logger.warning("%s(查找落点 %s)", _DIST_NOT_BUILT_HINT, directory)

    @app.get("/")
    async def dist_not_built() -> JSONResponse:
        return JSONResponse({"detail": _DIST_NOT_BUILT_HINT}, status_code=503)

    return app


def _register_owner_routes(app: FastAPI) -> None:
    """属主数据端点:快照(事件缓存)与审计查询(旁路缓冲)。"""

    @app.get("/api/snapshot")
    async def api_snapshot(request: Request, since: int = 0):
        owner: BackendOwner | None = getattr(request.app.state, "owner", None)
        if owner is None:
            return JSONResponse({"detail": _NO_OWNER_HINT}, status_code=503)
        events = [asdict(e) for e in owner.cache.snapshot(since)]
        return {"events": events, "latest_seq": owner.cache.latest_seq}

    @app.get("/api/audit")
    async def api_audit(request: Request, limit: int = 50,
                        thread_id: Optional[str] = None,
                        event: Optional[str] = None, since: int = 0):
        owner: BackendOwner | None = getattr(request.app.state, "owner", None)
        if owner is None:
            return JSONResponse({"detail": _NO_OWNER_HINT}, status_code=503)
        entries = owner.audit_tap.query(limit=limit, thread_id=thread_id,
                                        event=event, since=since)
        return {"entries": entries, "count": len(entries)}
