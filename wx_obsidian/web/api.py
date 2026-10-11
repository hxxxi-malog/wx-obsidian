"""FastAPI 应用工厂：REST + SSE + 安全中间件 + 静态资源托管。

依赖注入是刻意的：`create_app(runtime)` 接收已装配好的 WebRuntime，
测试时传 fake runtime，不需要真 API Key、真 vault、真数据源。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Body, FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from wx_obsidian.web import APP_NAME, APP_VERSION
from wx_obsidian.web import articles as articles_api
from wx_obsidian.web import fs as fs_api
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
_SSE_KEEPALIVE_SECONDS = 15.0

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
_BOOL_PATCH_KEYS = frozenset({"vision.enabled"})
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


def _extract_token(scope: Scope) -> str:
    """从请求头或查询参数取出 token（SSE 的 EventSource 无法设自定义头，故支持 query）。"""
    for key, value in scope.get("headers", []):
        if key.lower() == TOKEN_HEADER:
            return str(value.decode("latin-1"))
    query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    return str((query.get("token") or [""])[0])


class SecurityMiddleware:
    """纯 ASGI 安全中间件：来源必须是回环地址，且 /api/* 必须带正确 token。

    刻意不用 BaseHTTPMiddleware：它会包装响应流，对 SSE 长连接不友好。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        token: str,
        allowed_hosts: frozenset[str] = LOOPBACK_HOSTS,
        public_paths: frozenset[str] = PUBLIC_PATHS,
    ) -> None:
        self.app = app
        self._token = token
        self._allowed_hosts = allowed_hosts
        self._public_paths = public_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        host = str(client[0]) if client else ""
        if not is_loopback_host(host, self._allowed_hosts):
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
        "web": {"port": int(cm.get("web.port", 8765) or 8765)},
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


def create_app(
    runtime: WebRuntime,
    *,
    token: str | None = None,
    extra_allowed_hosts: frozenset[str] = frozenset(),
) -> FastAPI:
    """构造 FastAPI 应用。

    Args:
        runtime: 已装配的运行期依赖。
        token: 访问 token；省略时随机生成（生产由启动器生成并写盘）。
        extra_allowed_hosts: 额外放行来源（仅测试用，生产保持回环限制）。

    Returns:
        可直接交给 uvicorn 的应用实例。
    """
    resolved_token = token or secrets.token_urlsafe(24)
    app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url=None, redoc_url=None)
    jobs = JobManager(runtime.orchestrator)
    app.state.runtime = runtime
    app.state.jobs = jobs
    app.state.token = resolved_token

    app.add_middleware(
        SecurityMiddleware,
        token=resolved_token,
        allowed_hosts=LOOPBACK_HOSTS | extra_allowed_hosts,
    )

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
        return payload

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
    ) -> dict[str, Any]:
        return articles_api.list_articles(
            query=q, category=category, status=status, limit=limit, offset=offset
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

    @app.post("/api/articles/{article_id}/open")
    def open_article(article_id: str) -> Response:
        item = articles_api.get_article(article_id)
        if item is None:
            return JSONResponse({"error": "article_not_found"}, status_code=404)
        try:
            return JSONResponse(articles_api.open_in_obsidian(str(item["file_path"])))
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
