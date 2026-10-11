"""FastAPI 应用工厂：REST + SSE + 安全中间件 + 静态资源托管。

依赖注入是刻意的：`create_app(runtime)` 接收已装配好的 WebRuntime，
测试时传 fake runtime，不需要真 API Key、真 vault、真数据源。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import ipaddress
import json
import logging
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from wx_obsidian.web import APP_NAME, APP_VERSION, scheduling
from wx_obsidian.web import articles as articles_api
from wx_obsidian.web import fs as fs_api
from wx_obsidian.web import logs as logs_api
from wx_obsidian.web.jobs import JobBusyError, JobManager
from wx_obsidian.web.runtime import WebRuntime

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# 只允许本机回环来源，作为「只绑 127.0.0.1」之外的第二道防线
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# 免鉴权路径：/api/ping 只回应用标识，供第二实例探测
PUBLIC_PATHS = frozenset({"/api/ping"})

# 端点签名刻意不用 `X | None` / Optional：FastAPI 会在运行时求值端点注解，
# 而 py3.9 无法求值 `dict[str, Any] | None`（PEP 604 需 3.10）。可省 body 用
# Body(default_factory=dict) 表达，既保住可选语义又不引入联合类型。
TOKEN_HEADER = b"x-wx-token"
MASK = "****"

# SSE 轮询与心跳（进度实时性要求 ≤2s，0.25s 轮询留足余量）
_SSE_POLL_SECONDS = 0.25
# 退出接口：先让响应到达浏览器，再真正停服务
_SHUTDOWN_DELAY_SECONDS = 0.3
_SSE_KEEPALIVE_SECONDS = 15.0
# 批量删除单次上限（超过就分多次调用，避免一次请求长时间占住线程池）
_BULK_DELETE_MAX = 500

# 允许通过 PUT /api/config 写入的键（白名单：不接受任意配置路径）
_PLAIN_PATCH_KEYS = frozenset(
    {
        "llm.active",
        "llm.max_tokens",
        "llm.temperature",
        "vision.active",
        "vision.enabled",
        "vision.max_concurrency",
        "vision.timeout",
        "source.type",
        "source.base_url",
        "source.token",
        "source.timeout",
        "obsidian.vault_path",
        "obsidian.articles_dir",
        "fetch.max_days",
        "fetch.max_workers",
        "fetch.feed_limit",
        "web.port",
        "web.open_browser",
    }
)
_PROVIDER_PATCH_RE = re.compile(
    r"^(?:llm|vision)\.providers\.[^.]+\.(?:base_url|model|api_key|api_key_env)$"
)
_INT_PATCH_KEYS = frozenset(
    {
        "llm.max_tokens",
        "vision.max_concurrency",
        "vision.timeout",
        "source.timeout",
        "fetch.max_days",
        "fetch.max_workers",
        "fetch.feed_limit",
        "web.port",
    }
)
_BOOL_PATCH_KEYS = frozenset({"vision.enabled", "web.open_browser"})
_ALLOWED_ENV_KEYS = frozenset(
    {"DEEPSEEK_API_KEY", "VISION_API_KEY", "DEEPSEEK_BASE_URL", "MODEL_NAME"}
)


def is_loopback_host(host: str, allowed: frozenset[str] = LOOPBACK_HOSTS) -> bool:
    """判断来源地址是否为回环地址。

    Args:
        host: 客户端地址（ASGI scope 里的 client[0]）。
        allowed: 额外放行的主机名（测试注入 "testclient" 用）。

    Returns:
        是否允许访问。
    """
    if not host:
        return False
    if host in allowed:
        return True
    return host.startswith("127.")


def is_allowed_host(
    host: str,
    allowed: frozenset[str] = LOOPBACK_HOSTS,
    *,
    allow_remote: bool = False,
) -> bool:
    """来源是否放行。

    默认只放行回环；`allow_remote=True`（显式开关，见 `WX_ALLOW_REMOTE`）时
    追加放行私有网段与链路本地地址——容器场景下客户端 IP 是 Docker 网关
    （172.x），局域网场景是路由后的私有地址。**token 校验与这个开关无关，
    永远强制。**

    Args:
        host: 客户端地址。
        allowed: 额外放行的主机名。
        allow_remote: 是否放行私有网段。

    Returns:
        是否允许继续处理该请求。
    """
    if is_loopback_host(host, allowed):
        return True
    if not allow_remote or not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_link_local


def _extract_token(scope: Scope) -> str:
    """从请求头或查询参数取出 token（SSE 的 EventSource 无法设自定义头，故支持 query）。"""
    for key, value in scope.get("headers", []):
        if key.lower() == TOKEN_HEADER:
            return str(value.decode("latin-1"))
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    return str((query.get("token") or [""])[0])


class SecurityMiddleware:
    """纯 ASGI 安全中间件：来源必须是回环地址（或显式放行的私有网段），且 /api/* 必须带正确 token。

    刻意不用 BaseHTTPMiddleware：它会包装响应流，对 SSE 长连接不友好。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: frozenset[str] = LOOPBACK_HOSTS,
        public_paths: frozenset[str] = PUBLIC_PATHS,
        allow_remote: bool = False,
    ) -> None:
        self.app = app
        self._token = token
        self._allowed_hosts = allowed_hosts
        self._public_paths = public_paths
        self._allow_remote = allow_remote

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = str(client[0]) if client else ""
        if not is_allowed_host(host, self._allowed_hosts, allow_remote=self._allow_remote):
            logger.warning("拒绝非回环来源访问: %s %s", host, scope.get("path", ""))
            await self._reject(scope, receive, send, 403, "forbidden")
            return

        path = str(scope.get("path", ""))
        if path.startswith("/api") and path not in self._public_paths:
            provided = _extract_token(scope)
            if not provided or not secrets.compare_digest(provided, self._token):
                await self._reject(scope, receive, send, 401, "unauthorized")
                return

        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, status_code: int, error: str
    ) -> None:
        response = JSONResponse({"error": error}, status_code=status_code)
        await response(scope, receive, send)


def jsonable(value: Any) -> Any:
    """把 dataclass / datetime / Path 递归转成可 JSON 序列化的结构。"""
    if is_dataclass(value) and not isinstance(value, type):
        return {key: jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


def mask_secret(value: str) -> str:
    """密钥掩码：保留少量首尾便于辨认，绝不回传完整值。"""
    if not value:
        return ""
    if len(value) <= 8:
        return MASK
    return f"{value[:4]}{MASK}{value[-4:]}"


def is_masked(value: str) -> bool:
    """判断值是否为掩码串（是则保存时跳过，避免把掩码写回真密钥）。"""
    return MASK in value


def _providers_view(config_manager: Any, section: str) -> dict[str, dict[str, str]]:
    providers = config_manager.get(f"{section}.providers", {}) or {}
    view: dict[str, dict[str, str]] = {}
    if not isinstance(providers, dict):
        return view
    for name, channel in providers.items():
        if not isinstance(channel, dict):
            continue
        view[str(name)] = {
            "base_url": str(channel.get("base_url") or ""),
            "model": str(channel.get("model") or ""),
            "api_key": mask_secret(str(channel.get("api_key") or "")),
            "api_key_env": str(channel.get("api_key_env") or ""),
        }
    return view


def _config_snapshot(runtime: WebRuntime) -> dict[str, Any]:
    """配置快照（密钥一律掩码）。"""
    cm = runtime.config_manager
    llm = cm.resolve_llm_channel()
    vision = cm.resolve_vision_channel()
    return {
        "llm": {
            "active": str(cm.get("llm.active", "") or ""),
            "available": cm.channel_names("llm"),
            "providers": _providers_view(cm, "llm"),
            "effective": {
                "provider": llm.get("provider", ""),
                "base_url": llm.get("base_url", ""),
                "model": llm.get("model", ""),
                "api_key": mask_secret(str(llm.get("api_key") or "")),
            },
            "api_key": mask_secret(cm.get_env("DEEPSEEK_API_KEY")),
            "max_tokens": int(cm.get("llm.max_tokens", 4096) or 4096),
            "temperature": float(cm.get("llm.temperature", 0.7) or 0.7),
        },
        "vision": {
            "enabled": bool(cm.get("vision.enabled", True)),
            "active": str(cm.get("vision.active", "") or ""),
            "available": cm.channel_names("vision"),
            "providers": _providers_view(cm, "vision"),
            "effective": {
                "provider": vision.get("provider", ""),
                "base_url": vision.get("base_url", ""),
                "model": vision.get("model", ""),
                "api_key": mask_secret(str(vision.get("api_key") or "")),
            },
            "api_key": mask_secret(cm.get_env("VISION_API_KEY")),
            "max_concurrency": int(cm.get("vision.max_concurrency", 10) or 10),
            "timeout": int(cm.get("vision.timeout", 120) or 120),
        },
        "source": {
            "type": str(cm.get("source.type", "") or ""),
            "base_url": str(cm.get("source.base_url", "") or ""),
            "timeout": int(cm.get("source.timeout", 15) or 15),
            "token": mask_secret(str(cm.get("source.token", "") or "")),
        },
        "obsidian": {
            "vault_path": str(cm.get("obsidian.vault_path", "") or ""),
            "articles_dir": str(cm.get("obsidian.articles_dir", "公众号文章") or "公众号文章"),
        },
        "fetch": {
            "max_days": int(cm.get("fetch.max_days", 7) or 7),
            "max_workers": int(cm.get("fetch.max_workers", 5) or 5),
            "feed_limit": int(cm.get("fetch.feed_limit", 300) or 300),
        },
        "web": {
            "port": int(cm.get("web.port", 8765) or 8765),
            "open_browser": bool(cm.get("web.open_browser", True)),
        },
    }


def _coerce(key: str, raw: Any) -> Any:
    """按白名单把值转成正确类型。

    Raises:
        ValueError: 类型不合法。
    """
    if key in _BOOL_PATCH_KEYS:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValueError(f"{key} 需要布尔值")
    if key in _INT_PATCH_KEYS:
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 需要整数") from exc
    if key == "llm.temperature":
        try:
            return float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} 需要数字") from exc
    return str(raw)


# macOS 的 TCC 隐私保护会拦住后台进程读这些目录——
# 原 LaunchAgent 方案就是栽在「读不到 ~/Downloads」上（见 README）。
_TCC_RISKY_DIRS = frozenset({"Downloads", "Desktop"})


def _first_existing(path: Path) -> Path:
    """向上找到第一个存在的目录。

    真正要写的是 `<vault>/<articles_dir>`，它可能还不存在（首次运行会创建）。
    这时该检查的是**最近的存在祖先**是否可写——直接查 `vault_path` 会误判：
    容器里 `/Users/malog/Documents` 只是 Docker 为挂载点自动建的目录，
    而实际写入目标是挂在它下面的 `公众号文章`。
    """
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def vault_warnings(runtime: WebRuntime) -> list[dict[str, str]]:
    """Vault 路径的可操作告警：未设置 / 不存在 / 不可写 / 落在 TCC 敏感目录。

    这些不是运行时错误，而是「必须先处理」的前置条件，所以进 `/api/health`
    让首屏就能提示，而不是等用户抓取失败后才发现 vault 根本写不进去。
    """
    vault = str(runtime.config_manager.get("obsidian.vault_path", "") or "").strip()
    if not vault:
        return [
            {
                "code": "vault_unset",
                "message": "尚未设置 Obsidian Vault 路径，抓到的文章无处落盘",
                "action": "在「配置管理」里设置，或用「选择文件夹」挑一个",
            }
        ]

    path = Path(vault).expanduser()
    articles_dir = str(runtime.config_manager.get("obsidian.articles_dir", "") or "公众号文章")
    warnings: list[dict[str, str]] = []
    if not path.exists():
        warnings.append(
            {
                "code": "vault_missing",
                "message": f"Vault 路径不存在：{path}",
                "action": "检查路径拼写，或重新用「选择文件夹」选一次",
            }
        )
    elif not path.is_dir():
        warnings.append(
            {
                "code": "vault_not_dir",
                "message": f"Vault 路径不是目录：{path}",
                "action": "改成目录，或用「选择文件夹」重选",
            }
        )
    else:
        target = _first_existing(path / articles_dir)
        if not os.access(target, os.W_OK):
            # 措辞刻意克制：这一条只说明「本进程写不进去」，不能断言目录本身只读。
            # 沙箱、TCC 未授权、POSIX 权限、容器挂载缺失都表现为这一种现象。
            warnings.append(
                {
                    "code": "vault_readonly",
                    "message": f"当前进程写不进文章目录：{target}",
                    "action": "先用「选择文件夹」重选一次（顺带完成 macOS 授权）；"
                    "容器部署请确认 vault 已按同一路径挂载；仍不行再查目录属主与权限",
                }
            )

    if any(part in _TCC_RISKY_DIRS for part in path.parts):
        warnings.append(
            {
                "code": "vault_in_tcc_dir",
                "message": f"Vault 位于 {path}：macOS 隐私保护（TCC）会拦住后台任务读取 ~/Downloads、~/Desktop",
                "action": "建议迁到 ~/Documents 下；用「选择文件夹」重新选一次可即时授权",
            }
        )
    return warnings


def _token_expiry(token: str) -> str:
    """从一个 Bearer Token 里读 `exp`（只解码不验签，仅用于界面提示有效期）。

    we-mp-rss 的令牌是 JWT，默认有有效期（本项目的部署配方是 3 天）。不把到期
    时间显示出来的话，它过期后只会表现为「状态突然读不到了」，很难查。
    """
    parts = token.split(".")
    if len(parts) < 2:
        return ""
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, binascii.Error):
        return ""
    exp = decoded.get("exp") if isinstance(decoded, dict) else None
    if not isinstance(exp, (int, float)):
        return ""
    try:
        return datetime.fromtimestamp(float(exp), timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def source_token_state(runtime: WebRuntime) -> dict[str, Any]:
    """数据源访问令牌的状态（**不回显令牌本身**，只给是否配置与到期时间）。"""
    token = str(runtime.config_manager.get("source.token", "") or "").strip()
    if not token:
        return {"configured": False, "expires_at": "", "expired": False}

    expires_at = _token_expiry(token)
    expired = False
    if expires_at:
        try:
            expired = datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
        except ValueError:
            expired = False
    return {"configured": True, "expires_at": expires_at, "expired": expired}


def _account_payload(runtime: WebRuntime) -> dict[str, Any]:
    """账号状态 + 令牌状态：界面据此区分「未登录」与「查不到」。"""
    payload = dict(jsonable(runtime.orchestrator.get_account_status()))
    payload["token"] = source_token_state(runtime)
    return payload


def create_app(
    runtime: WebRuntime,
    *,
    token: str | None = None,
    extra_allowed_hosts: frozenset[str] = frozenset(),
    on_quit: Callable[[], None] | None = None,
    allow_remote: bool = False,
) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        runtime: 已装配的运行期依赖。
        token: 访问 token；省略时随机生成（生产由启动器生成并写盘）。
        extra_allowed_hosts: 额外放行来源（仅测试用，生产保持回环限制）。
        on_quit: 「退出应用」回调（启动器传入 stop.set）。省略时该接口返回 501——
            窗口化的 .app 没有 Cocoa 事件循环，系统菜单的 Cmd+Q 到不了本进程，
            所以界面上必须有一个能真正退出的入口。
        allow_remote: 是否放行私有网段来源（默认否）。容器/局域网部署由启动器的
            `WX_ALLOW_REMOTE` 显式打开；打开后 token 校验依然强制。

    Returns:
        可直接交给 uvicorn 的应用实例。
    """
    resolved_token = token or secrets.token_urlsafe(24)
    log_buffer = logs_api.install_log_buffer()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """启动时恢复用户的定时任务设定；关闭时停掉调度器与日志缓冲。"""
        try:
            scheduling.restore_state(runtime, jobs)
        except Exception:  # 恢复失败不能拦住服务启动
            logger.warning("恢复定时任务失败", exc_info=True)
        try:
            yield
        finally:
            runtime.scheduler.stop()
            logs_api.uninstall_log_buffer(log_buffer)

    app = FastAPI(
        title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None, lifespan=lifespan
    )
    jobs = JobManager(runtime.orchestrator)
    # 日志同时推给正在跑的任务，前端就能在进度面板里看到告警
    log_buffer.subscribe(
        lambda entry: jobs.emit_log(entry.message, level=entry.level, logger_name=entry.logger)
    )
    app.state.runtime = runtime
    app.state.jobs = jobs
    app.state.logs = log_buffer
    app.state.token = resolved_token

    app.add_middleware(
        SecurityMiddleware,
        token=resolved_token,
        allowed_hosts=LOOPBACK_HOSTS | extra_allowed_hosts,
        allow_remote=allow_remote,
    )
    app.state.allow_remote = allow_remote

    # -- 系统与状态 ---------------------------------------------------------

    @app.get("/api/ping")
    async def ping() -> dict[str, Any]:
        """免鉴权存活探测：第二实例据此判断「已在运行」。"""
        return {"app": APP_NAME, "version": APP_VERSION}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        """四项组件健康状态（同步 def，由 Starlette 放到线程池执行，不阻塞事件循环）。"""
        status = runtime.orchestrator.get_health_status()
        payload: dict[str, Any] = jsonable(status)
        payload["overall"] = status.overall
        # 首启引导与环境告警：前端首屏据此决定是否跳设置页
        payload["first_run"] = runtime.config_manager.is_first_run()
        payload["warnings"] = vault_warnings(runtime)
        return payload

    @app.post("/api/shutdown")
    async def shutdown() -> Response:
        """退出应用（窗口化的 .app 没有 Cocoa 事件循环，Cmd+Q 到不了本进程）。"""
        if on_quit is None:
            return JSONResponse(
                {"error": "not_supported", "detail": "当前启动方式不支持从页面退出"},
                status_code=501,
            )
        # 先回响应再退出，否则浏览器拿到的是连接被重置
        asyncio.get_running_loop().call_later(_SHUTDOWN_DELAY_SECONDS, on_quit)
        return JSONResponse({"ok": True})

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        """处理统计信息。"""
        return dict(jsonable(runtime.orchestrator.get_statistics()))

    @app.get("/api/failed")
    def failed() -> list[dict[str, Any]]:
        """失败文章列表。"""
        return [dict(item) for item in jsonable(runtime.orchestrator.get_failed_articles())]

    # -- 任务与事件 ---------------------------------------------------------

    @app.post("/api/jobs")
    async def submit_job(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        body = payload or {}
        try:
            job_id = await jobs.submit(
                str(body.get("kind") or "fetch"),
                limit=int(body.get("limit") or 0),
                force=bool(body.get("force")),
                article_id=str(body.get("article_id") or ""),
                article_ids=[str(a) for a in (body.get("article_ids") or [])],
            )
        except JobBusyError as exc:
            return JSONResponse({"error": "job_busy", "detail": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
        return JSONResponse({"job_id": job_id}, status_code=202)

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        current = jobs.current()
        return {
            "current": current.to_dict() if current else None,
            "recent": [job.to_dict() for job in jobs.recent()],
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> Response:
        snapshot = jobs.snapshot(job_id)
        if snapshot is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)
        return JSONResponse(snapshot.to_dict())

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> Response:
        if not jobs.cancel(job_id):
            return JSONResponse({"error": "not_running"}, status_code=409)
        return JSONResponse({"ok": True})

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request) -> Response:
        """SSE 进度流；支持 Last-Event-ID 断线重连回放。"""
        if jobs.get(job_id) is None:
            return JSONResponse({"error": "job_not_found"}, status_code=404)

        header_seq = (
            request.headers.get("last-event-id") or request.query_params.get("last_id") or "0"
        )
        try:
            start_seq = int(header_seq)
        except ValueError:
            start_seq = 0

        async def stream() -> AsyncIterator[str]:
            # 刻意用短间隔轮询而不是 asyncio.Event 唤醒：Event 有事件循环亲和性，
            # 跨请求/跨 loop 复用会炸；0.25s 的延迟远低于 2s 的进度实时性要求。
            last_seq = start_seq
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                pending = await jobs.events_after(job_id, last_seq)
                for event in pending:
                    last_seq = event.seq
                    body = json.dumps(event.to_dict(), ensure_ascii=False)
                    yield f"id: {event.seq}\nevent: job\ndata: {body}\n\n"

                snapshot = jobs.snapshot(job_id)
                if snapshot is None:
                    return
                if snapshot.state != "running" and not pending:
                    return

                await asyncio.sleep(_SSE_POLL_SECONDS)
                if pending:
                    idle_ticks = 0
                else:
                    idle_ticks += 1
                    if idle_ticks * _SSE_POLL_SECONDS >= _SSE_KEEPALIVE_SECONDS:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- 文章 ---------------------------------------------------------------

    @app.get("/api/articles")
    def list_articles(
        q: str = "",
        category: str = "",
        status: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "",
        order: str = "",
    ) -> dict[str, Any]:
        return articles_api.list_articles(
            query=q,
            category=category,
            status=status,
            limit=limit,
            offset=offset,
            sort=sort,
            order=order,
        )

    @app.get("/api/articles/{article_id}")
    def article_detail(article_id: str) -> Response:
        item = articles_api.get_article(article_id)
        if item is None:
            return JSONResponse({"error": "article_not_found"}, status_code=404)
        return JSONResponse(item)

    @app.delete("/api/articles/{article_id}")
    def delete_article(article_id: str) -> Response:
        try:
            actions = articles_api.delete_article(article_id, runtime.config_manager)
        except ValueError as exc:
            return JSONResponse({"error": "not_configured", "detail": str(exc)}, status_code=400)
        if actions is None:
            return JSONResponse({"error": "article_not_found"}, status_code=404)
        return JSONResponse({"ok": True, "actions": actions})

    @app.post("/api/articles/bulk-delete")
    async def bulk_delete_articles(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> Response:
        """批量级联删除（去重后按输入顺序串行执行，放工作线程跑，不阻塞事件循环）。

        与单条 DELETE 的两处差别：运行中的任务先拦下（避免争抢 processed.json / MOC），
        未知 id 不整体失败而是逐条汇报。
        """
        raw_ids = (payload or {}).get("article_ids")
        if not isinstance(raw_ids, list):
            return JSONResponse(
                {"error": "bad_request", "detail": "article_ids 必须是数组"}, status_code=400
            )
        if not raw_ids:
            return JSONResponse(
                {"error": "bad_request", "detail": "article_ids 不能为空"}, status_code=400
            )
        if len(raw_ids) > _BULK_DELETE_MAX:
            return JSONResponse(
                {
                    "error": "bad_request",
                    "detail": f"一次最多删除 {_BULK_DELETE_MAX} 条，收到 {len(raw_ids)} 条",
                },
                status_code=400,
            )
        if jobs.busy:
            return JSONResponse(
                {
                    "error": "job_busy",
                    "detail": "有任务在跑，避免争抢 processed.json / MOC 写入；请等任务结束后重试",
                },
                status_code=409,
            )

        article_ids: list[str] = []
        seen: set[str] = set()
        for raw in raw_ids:
            article_id = str(raw)
            if article_id not in seen:  # 去重且保持输入顺序
                seen.add(article_id)
                article_ids.append(article_id)

        deleted = 0
        failed: list[dict[str, str]] = []
        actions: list[str] = []
        for article_id in article_ids:
            try:
                result = await asyncio.to_thread(
                    articles_api.delete_article, article_id, runtime.config_manager
                )
            except ValueError as exc:
                # 与单条 DELETE 一致：vault 未配置 → 400 not_configured
                return JSONResponse(
                    {"error": "not_configured", "detail": str(exc)}, status_code=400
                )
            except Exception as exc:  # 单条意外失败不影响其余条目，逐条计入 failed
                logger.warning("批量删除失败: %s", article_id, exc_info=True)
                failed.append({"article_id": article_id, "error": f"{type(exc).__name__}: {exc}"})
                continue
            if result is None:
                failed.append({"article_id": article_id, "error": "article_not_found"})
                continue
            deleted += 1
            for action in result:  # 摘要去重，保持首次出现的顺序
                if action not in actions:
                    actions.append(action)

        return JSONResponse({"ok": True, "deleted": deleted, "failed": failed, "actions": actions})

    @app.post("/api/articles/{article_id}/open")
    def open_article(article_id: str) -> Response:
        item = articles_api.get_article(article_id)
        if item is None:
            return JSONResponse({"error": "article_not_found"}, status_code=404)
        try:
            return JSONResponse(articles_api.open_in_obsidian(str(item["file_path"])))
        except articles_api.VaultNotRegisteredError as exc:
            # 单独一个错误码：前端要把「去 Obsidian 注册仓库」的指引完整显示出来
            return JSONResponse(
                {"error": "vault_not_registered", "detail": str(exc)}, status_code=400
            )
        except (FileNotFoundError, RuntimeError) as exc:
            return JSONResponse({"error": "open_failed", "detail": str(exc)}, status_code=400)

    @app.post("/api/articles/retry")
    async def retry_articles(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        article_ids = [str(a) for a in ((payload or {}).get("article_ids") or [])]
        if not article_ids:
            return JSONResponse(
                {"error": "bad_request", "detail": "article_ids 不能为空"}, status_code=400
            )
        try:
            job_id = await jobs.submit("retry", article_ids=article_ids)
        except JobBusyError as exc:
            return JSONResponse({"error": "job_busy", "detail": str(exc)}, status_code=409)
        return JSONResponse({"job_id": job_id}, status_code=202)

    # -- 配置 ---------------------------------------------------------------

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        return _config_snapshot(runtime)

    @app.put("/api/config")
    def put_config(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        cm = runtime.config_manager
        body = payload or {}
        patch = body.get("patch") or {}
        env = body.get("env") or {}
        if not isinstance(patch, dict) or not isinstance(env, dict):
            return JSONResponse(
                {"error": "bad_request", "detail": "patch/env 必须是对象"}, status_code=400
            )

        unknown = [
            key
            for key in patch
            if key not in _PLAIN_PATCH_KEYS and not _PROVIDER_PATCH_RE.match(key)
        ]
        unknown += [key for key in env if key not in _ALLOWED_ENV_KEYS]
        if unknown:
            return JSONResponse(
                {"error": "bad_request", "detail": f"不支持的配置键: {', '.join(sorted(unknown))}"},
                status_code=400,
            )

        skipped: list[str] = []
        applied: list[str] = []
        for key, raw in patch.items():
            text = "" if raw is None else str(raw)
            if text == "" or is_masked(text):
                skipped.append(key)  # 留空/掩码一律视为「不修改」，避免清掉已存密钥
                continue
            if key in ("llm.active", "vision.active"):
                section = key.split(".")[0]
                if not cm.set_active_channel(section, text):
                    return JSONResponse(
                        {
                            "error": "bad_request",
                            "detail": f"{section} 渠道「{text}」不存在（可用：{', '.join(cm.channel_names(section)) or '无'}）",
                        },
                        status_code=400,
                    )
                applied.append(key)
                continue
            try:
                cm.set(key, _coerce(key, raw))
            except ValueError as exc:
                return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
            applied.append(key)

        for key, raw in env.items():
            text = "" if raw is None else str(raw)
            if text == "" or is_masked(text):
                skipped.append(key)
                continue
            cm.set_env(key, text)
            applied.append(key)

        if applied:
            cm.save()

        restart_required = any(key.startswith("source.") for key in applied)
        return JSONResponse(
            {
                "saved": bool(applied),
                "applied": applied,
                "skipped": skipped,
                "restart_required": restart_required,
            }
        )

    @app.post("/api/config/test")
    def test_config(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        target = str((payload or {}).get("target") or "all")
        cm = runtime.config_manager
        try:
            if target == "all":
                results = cm.test_all_connections()
            elif target == "llm":
                results = {"llm": cm.test_llm_connection()}
            elif target == "vision":
                results = {"vision": cm.test_vision_connection()}
            elif target == "source":
                results = {"source": cm.test_source_connection()}
            else:
                return JSONResponse(
                    {"error": "bad_request", "detail": f"未知 target: {target}"}, status_code=400
                )
        except Exception as exc:  # 连通性测试失败要给前端可读信息，而不是 500
            return JSONResponse(
                {"error": "test_failed", "detail": f"{type(exc).__name__}: {exc}"}, status_code=200
            )
        return JSONResponse(jsonable(results))

    # -- 数据源 / 公众号 / 账号 ---------------------------------------------

    @app.get("/api/source/health")
    def source_health() -> dict[str, Any]:
        """数据源服务探活（同步 def → 由线程池执行，不阻塞事件循环）。"""
        try:
            healthy = runtime.orchestrator.is_source_healthy()
        except Exception as exc:  # 探活失败就是「不健康」，不该 500
            logger.warning("数据源探活失败: %s", exc)
            healthy = False
        return {
            "healthy": healthy,
            "type": str(runtime.config_manager.get("source.type", "") or ""),
            "base_url": str(runtime.config_manager.get("source.base_url", "") or ""),
            "docker_hint": "cd deploy/we-mp-rss && docker compose up -d --build",
        }

    @app.get("/api/source/account")
    def source_account() -> Response:
        try:
            return JSONResponse(_account_payload(runtime))
        except Exception as exc:
            return JSONResponse(
                {"error": "account_unavailable", "detail": f"{type(exc).__name__}: {exc}"},
                status_code=200,
            )

    @app.get("/api/source/login-url")
    def source_login_url() -> dict[str, Any]:
        try:
            return {"url": runtime.orchestrator.get_login_url()}
        except Exception as exc:
            return {"url": "", "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/source/refresh")
    def source_refresh() -> dict[str, Any]:
        """重新探测服务可达性并**重读**登录态。

        刻意不叫「刷新登录态」：微信读书 Cookie 由 we-mp-rss 自己维护，本项目不持有
        凭据，能做的只有重新读取。返回里带上实际的账号快照，界面才不会把
        「服务可达」误报成「登录态已刷新」。
        """
        healthy = False
        error = ""
        try:
            healthy = bool(runtime.orchestrator.refresh_cookie())
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        account: dict[str, Any] = {}
        try:
            account = _account_payload(runtime)
        except Exception as exc:
            error = error or f"{type(exc).__name__}: {exc}"
        return {"ok": healthy, "error": error, "account": account}

    @app.get("/api/feeds")
    def list_feeds() -> Response:
        try:
            return JSONResponse(jsonable(runtime.orchestrator.get_feeds()))
        except Exception as exc:
            return JSONResponse(
                {"error": "feeds_unavailable", "detail": f"{type(exc).__name__}: {exc}"},
                status_code=200,
            )

    @app.post("/api/feeds")
    def add_feed(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        article_url = str((payload or {}).get("article_url") or "").strip()
        if not article_url:
            return JSONResponse(
                {"error": "bad_request", "detail": "缺少 article_url"}, status_code=400
            )
        try:
            feed = runtime.orchestrator.add_feed(article_url)
        except Exception as exc:
            return JSONResponse(
                {"error": "add_feed_failed", "detail": f"{type(exc).__name__}: {exc}"},
                status_code=200,
            )
        if feed is None:
            return JSONResponse(
                {
                    "error": "not_supported",
                    "detail": "当前数据源不支持按文章链接添加公众号，请在 we-mp-rss 界面里订阅",
                },
                status_code=501,
            )
        return JSONResponse(jsonable(feed))

    @app.delete("/api/feeds/{feed_id}")
    def delete_feed(feed_id: str) -> Response:
        try:
            ok = bool(runtime.orchestrator.delete_feed(feed_id))
        except Exception as exc:
            return JSONResponse(
                {"error": "delete_feed_failed", "detail": f"{type(exc).__name__}: {exc}"},
                status_code=200,
            )
        if not ok:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse({"ok": True})

    # -- 定时任务 -----------------------------------------------------------

    @app.get("/api/scheduler/jobs")
    def scheduler_jobs() -> dict[str, Any]:
        return {
            "jobs": scheduling.job_list(runtime),
            "state": scheduling.read_state(runtime.config_manager).to_dict(),
        }

    @app.post("/api/scheduler/jobs")
    async def set_scheduler_job(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        """新增/修改/停止定时任务。

        body: {"kind": "fetch", "interval": "2h"} 或 {"kind": "fetch", "interval": "0"}
              {"kind": "keepalive", "enabled": true|false}
        """
        body = payload or {}
        kind = str(body.get("kind") or "")
        fetch_hours: int | None = None
        keepalive_enabled: bool | None = None

        if kind == "fetch":
            raw = str(body.get("interval") or "").strip().lower().removesuffix("h")
            try:
                fetch_hours = int(raw or 0)
            except ValueError:
                return JSONResponse(
                    {"error": "bad_request", "detail": f"无法解析间隔: {body.get('interval')}"},
                    status_code=400,
                )
        elif kind == "keepalive":
            keepalive_enabled = bool(body.get("enabled", True))
        else:
            return JSONResponse(
                {"error": "bad_request", "detail": "kind 需为 fetch 或 keepalive"}, status_code=400
            )

        try:
            state = scheduling.apply_state(
                runtime, jobs, fetch_hours=fetch_hours, keepalive_enabled=keepalive_enabled
            )
        except ValueError as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
        return JSONResponse(
            {"ok": True, "state": state.to_dict(), "jobs": scheduling.job_list(runtime)}
        )

    @app.delete("/api/scheduler/jobs/{job_id}")
    def delete_scheduler_job(job_id: str) -> Response:
        if job_id == scheduling.FETCH_JOB_ID:
            state = scheduling.apply_state(runtime, jobs, fetch_hours=0)
        elif job_id == scheduling.KEEPALIVE_JOB_ID:
            state = scheduling.apply_state(runtime, jobs, keepalive_enabled=False)
        else:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return JSONResponse(
            {"ok": True, "state": state.to_dict(), "jobs": scheduling.job_list(runtime)}
        )

    @app.post("/api/scheduler/jobs/{job_id}/run-now")
    async def run_scheduler_job(job_id: str) -> Response:
        """立即执行一次。

        刻意不走 APScheduler 的 `run_job_now`：直接提交到 JobManager，
        这样立刻生效、受单飞约束，而且进度能在页面上看到。
        """
        if runtime.scheduler.get_job_status(job_id) is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        if job_id == scheduling.FETCH_JOB_ID:
            if jobs.busy:
                return JSONResponse(
                    {"error": "job_busy", "detail": "已有任务在运行"}, status_code=409
                )
            new_job_id = await jobs.submit("fetch")
            return JSONResponse({"ok": True, "job_id": new_job_id})
        await asyncio.to_thread(runtime.orchestrator.refresh_cookie)
        return JSONResponse({"ok": True})

    # -- 运行日志 -----------------------------------------------------------

    @app.get("/api/logs")
    def get_logs(since: int = 0) -> dict[str, Any]:
        entries = [entry.to_dict() for entry in log_buffer.entries_since(since)]
        return {"entries": entries, "last_seq": log_buffer.last_seq}

    @app.get("/api/logs/events")
    async def log_events(request: Request, since: int = 0) -> Response:
        """日志 SSE 流：先回放历史，再持续推送新条目，客户端断开即结束。

        断线续传的 seq 优先级：`Last-Event-ID` 头（EventSource 重连自动带）>
        `?last_id=`（后备）> `?since=`；值不可解析时视为未提供。
        """
        start_seq = int(since)
        for raw in (
            request.headers.get("last-event-id"),
            request.query_params.get("last_id"),
        ):
            if not raw:
                continue
            try:
                start_seq = int(raw)
                break
            except ValueError:
                continue

        async def stream() -> AsyncIterator[str]:
            # 与 /api/jobs/{job_id}/events 同一套短轮询写法（而不是 asyncio.Event：
            # Event 有事件循环亲和性，跨请求/跨 loop 复用会炸）
            buffer: logs_api.LogBuffer = request.app.state.logs
            last_seq = start_seq
            idle_ticks = 0
            while True:
                if await request.is_disconnected():
                    return
                pending = buffer.entries_since(last_seq)
                for entry in pending:
                    last_seq = entry.seq
                    body = json.dumps(entry.to_dict(), ensure_ascii=False)
                    yield f"id: {entry.seq}\nevent: log\ndata: {body}\n\n"

                await asyncio.sleep(_SSE_POLL_SECONDS)
                if pending:
                    idle_ticks = 0
                else:
                    idle_ticks += 1
                    if idle_ticks * _SSE_POLL_SECONDS >= _SSE_KEEPALIVE_SECONDS:
                        idle_ticks = 0
                        yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # -- 文件系统 -----------------------------------------------------------

    @app.get("/api/fs/list")
    def fs_list(path: str = Query(default="")) -> Response:
        try:
            return JSONResponse(fs_api.list_dir(path))
        except ValueError as exc:
            return JSONResponse({"error": "bad_request", "detail": str(exc)}, status_code=400)
        except FileNotFoundError as exc:
            return JSONResponse({"error": "not_found", "detail": str(exc)}, status_code=404)
        except NotADirectoryError as exc:
            return JSONResponse({"error": "not_a_directory", "detail": str(exc)}, status_code=400)
        except PermissionError as exc:
            return JSONResponse({"error": "forbidden", "detail": str(exc)}, status_code=403)

    @app.post("/api/fs/pick")
    def fs_pick(payload: dict[str, Any] = Body(default_factory=dict)) -> Response:
        """弹 macOS 原生「选择文件夹」面板，返回绝对路径。

        这是浏览器沙箱拿不到绝对路径的正解（spec §4.8）：用户在系统面板里
        主动选中的目录，会顺带为该 app 建立 TCC 访问许可。
        """
        prompt = str((payload or {}).get("prompt") or "选择 Obsidian Vault")
        try:
            chosen = fs_api.pick_directory(prompt)
        except fs_api.PickerUnavailableError as exc:
            return JSONResponse(
                {"error": "picker_unavailable", "detail": str(exc)}, status_code=501
            )
        except fs_api.PickerError as exc:
            return JSONResponse({"error": "picker_failed", "detail": str(exc)}, status_code=500)
        return JSONResponse({"path": chosen, "cancelled": not chosen})

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """托管 Vite 构建产物，并为 SPA 路由做 index.html 回退。"""
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        if full_path.startswith("api/"):
            return JSONResponse({"error": "not_found"}, status_code=404)

        index_file = STATIC_DIR / "index.html"
        if not index_file.is_file():
            return JSONResponse(
                {
                    "error": "frontend_not_built",
                    "detail": "前端未构建：cd wx_obsidian/web/frontend && pnpm install && pnpm build",
                },
                status_code=503,
            )

        if full_path:
            root = STATIC_DIR.resolve()
            candidate = (root / full_path).resolve()
            # 防路径穿越：解析后必须仍在 static/ 之内
            if candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)

        return FileResponse(index_file)
