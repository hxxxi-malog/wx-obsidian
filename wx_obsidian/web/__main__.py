"""Web 模式启动器：起本地服务 → 等就绪 → 打开浏览器 → 常驻。

刻意只绑 127.0.0.1；token 存 `~/.wx-obsidian/web.token`（0600），
供第二实例启动时复用（否则新开的标签页会因缺 token 而被拒）。

三条设计：

- **单实例**（spec §4.6）：先 `GET /api/ping` 判断端口上是自己人还是别人。
  是自己人 → 只开浏览器后退出；是别人 → 立刻报错，而不是等 10 秒超时。
  端口占用本身就是进程级互斥，不需要锁文件。
- **日志必须落盘**（见 `web/logging_setup.py`）：容器、systemd、nohup 下都没有
  终端，启动期失败若不写日志，用户只会看到「起来了，但没反应」。
- **退出**：SIGINT/SIGTERM + `atexit` 都会优雅停；另外暴露 `POST /api/shutdown`
  供网页里的「退出应用」按钮调用——容器里它就是「停服务」的入口。
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import logging
import os
import secrets
import signal
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path
from types import FrameType

import requests
import uvicorn

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.paths import CONFIG_DIR
from wx_obsidian.web import APP_NAME, APP_VERSION
from wx_obsidian.web.api import create_app
from wx_obsidian.web.logging_setup import (
    configure_logging,
    default_log_file,
    mask_secrets,
    quiet_noisy_libraries,
)
from wx_obsidian.web.runtime import build_runtime

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
TOKEN_FILE = CONFIG_DIR / "web.token"
READY_TIMEOUT_SECONDS = 10.0
# 容器里必须绑 0.0.0.0，否则发布出去的端口连不到服务（见 docs/design-docs/web-ui/docker.md）
FLAG_NAMES = {"1", "true", "yes", "on"}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m wx_obsidian.web", description="wx-obsidian Web 模式"
    )
    parser.add_argument("--port", type=int, default=0, help="监听端口（0=读配置，默认 8765）")
    parser.add_argument(
        "--host",
        default="",
        help=f"监听地址（默认 {HOST}；容器里要填 0.0.0.0，也可用 WX_WEB_HOST）",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="放行私有网段来源（默认只允许回环；也可用 WX_ALLOW_REMOTE=1）",
    )
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument(
        "--token",
        default="",
        help="固定访问令牌（默认随机生成；长期运行请用 WX_WEB_TOKEN）",
    )
    parser.add_argument("--log-file", default="", help="覆盖日志文件路径（排错用）")
    return parser.parse_args(argv)


def _env_flag(name: str) -> bool:
    """把 `WX_xxx=1/true/yes/on` 这类环境变量解析成布尔。"""
    return os.environ.get(name, "").strip().lower() in FLAG_NAMES


def _connect_host(bind_host: str) -> str:
    """把监听地址转成「能用来连」的地址（0.0.0.0 不能拿去 connect）。"""
    return HOST if bind_host in ("", "0.0.0.0", "::", "[::]") else bind_host


def _in_container() -> bool:
    """是否跑在容器里。

    容器内看到的「本机地址」是自己那份 172.x 网卡，局域网根本连不上——
    把它当成访问地址提示出去是误导，所以容器里不出局域网地址。
    """
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _local_ipv4s() -> list[str]:
    """本机可用于访问的 IPv4（排掉回环），供「绑定 0.0.0.0」时提示实际访问地址。"""
    found: list[str] = []
    # UDP connect 不会真的发包，只用来问内核「走哪张网卡」
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("8.8.8.8", 80))
        found.append(str(probe.getsockname()[0]))
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    unique: list[str] = []
    for addr in found:
        if addr and addr not in unique and not addr.startswith("127."):
            unique.append(addr)
    return unique


def _write_token_file(token: str) -> None:
    """写 token 文件供第二个实例复用。

    写不进去（只读 HOME、权限异常等）不该让本次启动失败：
    token 本来就在内存里，落盘只是为了让新开的标签页/第二实例能复用。
    """
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token, encoding="utf-8")
        TOKEN_FILE.chmod(0o600)
    except OSError:
        logger.warning("无法写入 %s，第二个实例将无法复用 token", TOKEN_FILE, exc_info=True)


def _read_token_file() -> str:
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _clear_token_file() -> None:
    with contextlib.suppress(OSError):
        TOKEN_FILE.unlink()


def _ping(port: int, host: str = HOST) -> bool:
    """探测该端口上是否已有 wx-obsidian 实例。"""
    try:
        response = requests.get(f"http://{_connect_host(host)}:{port}/api/ping", timeout=1.0)
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("app") == APP_NAME


def _can_bind(port: int, host: str = HOST) -> bool:
    """地址:端口能否被本进程绑定（与 uvicorn 一致地设 SO_REUSEADDR，避免误报）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _wait_ready(port: int, timeout: float = READY_TIMEOUT_SECONDS, host: str = HOST) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ping(port, host):
            return True
        time.sleep(0.2)
    return False


def _build_url(port: int, token: str = "", *, first_run: bool = False, host: str = HOST) -> str:
    """拼访问地址。首启直接落到设置页，省掉「先去哪」的猜谜。"""
    path = "/settings" if first_run else "/"
    params: list[str] = []
    if token:
        params.append(f"token={token}")
    if first_run:
        params.append("first_run=1")
    query = "&".join(params)
    return f"http://{_connect_host(host)}:{port}{path}" + (f"?{query}" if query else "")


def _resolve_port(configured: int) -> int:
    return configured if configured > 0 else DEFAULT_PORT


def _existing_instance_url(port: int, host: str = HOST) -> str:
    """已有实例的访问地址；没有实例时返回空串（单实例判定的唯一入口）。"""
    if not _ping(port, host):
        return ""
    return _build_url(port, _read_token_file(), host=host)


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        logger.warning("无法自动打开浏览器，请手动访问上面的地址")


def _install_signal_handlers(stop: threading.Event) -> None:
    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        logger.info("收到信号 %s，准备退出", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            # 非主线程注册信号处理器会抛 ValueError；此时靠 stop 直接退出
            logger.debug("无法注册信号 %s（非主线程）", sig)


def _describe_log(log_path: Path) -> str:
    """日志提示必须诚实：写不进去时不能还指着一个不存在的文件让人去查。"""
    if log_path.is_file():
        return f"日志：{log_path}"
    return f"日志未能落盘（{log_path} 不可写），本次只有终端输出"


def _startup_failure(title: str, message: str, log_path: Path) -> int:
    logger.error("%s：%s", title, message)
    print(f"{title}：{message}", file=sys.stderr, flush=True)
    print(_describe_log(log_path), file=sys.stderr, flush=True)
    return 1


def _resolve_token(explicit: str = "") -> str:
    """访问令牌：优先环境变量（容器/长期运行需要稳定令牌），否则随机生成。

    随机令牌的问题：服务一重启，之前收藏的 URL 就失效。容器会重启，所以
    `WX_WEB_TOKEN` 是容器部署的标准做法。
    """
    from_env = os.environ.get("WX_WEB_TOKEN", "").strip()
    return explicit or from_env or secrets.token_urlsafe(24)


def _announce_urls(port: int, token: str, *, first_run: bool, bind_host: str) -> str:
    """打印访问地址，返回首选地址。

    绑 0.0.0.0 时不能把 `http://0.0.0.0:8765` 当成访问地址丢给用户——那不是
    一个能打开的地址；本机用 127.0.0.1，同时列出局域网地址供手机/其它设备用。
    """
    primary = _build_url(port, token, first_run=first_run, host=bind_host)
    print(f"{APP_NAME} v{APP_VERSION} 已启动：{primary}", flush=True)
    logger.info("%s v%s 已启动：%s", APP_NAME, APP_VERSION, mask_secrets(primary))

    if bind_host not in ("", "0.0.0.0", "::", "[::]"):
        return primary
    if _in_container():
        print(
            "  容器内监听 0.0.0.0：对外访问地址请用宿主机的 IP 或 127.0.0.1（容器自己的网卡地址局域网连不上）",
            flush=True,
        )
        return primary
    for addr in _local_ipv4s():
        extra = _build_url(port, token, first_run=first_run, host=addr)
        print(f"  局域网访问：{extra}", flush=True)
        logger.info("局域网访问：%s", mask_secrets(extra))
    return primary


def _serve(
    args: argparse.Namespace,
    cm: ConfigManager,
    *,
    bind_host: str,
    port: int,
    log_path: Path,
    allow_remote: bool,
) -> int:
    """真正起服务的那条路径（单实例判定与端口检查已在调用方完成）。"""
    open_browser = bool(cm.get("web.open_browser", True)) and not args.no_open
    first_run = cm.is_first_run()

    token = _resolve_token(args.token)
    _write_token_file(token)

    stop = threading.Event()
    _install_signal_handlers(stop)
    runtime = build_runtime(cm)
    quiet_noisy_libraries()
    # 自动采集间隔是启动期设置：改配置后需要重启（配置页会提示 restart_required）
    auto_sync_enabled = bool(cm.get("source.auto_sync", True))
    sync_interval_minutes = int(cm.get("source.sync_interval_minutes", 30) or 30)
    sync_interval_minutes = max(5, min(1440, sync_interval_minutes))
    source_sync_seconds = float(sync_interval_minutes * 60) if auto_sync_enabled else 0.0
    app = create_app(
        runtime,
        token=token,
        on_quit=stop.set,
        allow_remote=allow_remote,
        source_sync_interval_seconds=source_sync_seconds,
    )
    if source_sync_seconds > 0:
        logger.info("数据源自动采集已启用：每 %d 分钟触发一轮", sync_interval_minutes)
    else:
        logger.info("数据源自动采集已关闭（source.auto_sync=false）")
    log_level = str(cm.get("web.log_level", "info") or "info").lower()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=bind_host,
            port=port,
            log_level=log_level,
            access_log=False,
            # log_config=None：不让 uvicorn 自建 handler。它的默认配置把
            # propagate 设成 False 并直接写 stderr，结果是 uvicorn 的 INFO
            # 被我们的 stderr 重定向当成 ERROR 记进日志——真错误会被淹没。
            # 交给 root logger（= 我们的轮转文件 handler）统一处理。
            log_config=None,
        )
    )

    atexit.register(_emergency_stop, server, stop)

    thread = threading.Thread(target=server.run, name="wx-obsidian-uvicorn", daemon=True)
    thread.start()

    if not _wait_ready(port, host=bind_host):
        server.should_exit = True
        _clear_token_file()
        return _startup_failure(
            "wx-obsidian 启动失败",
            f"服务在 {READY_TIMEOUT_SECONDS:.0f} 秒内没有就绪（{bind_host}:{port}）。"
            f"常见原因：端口被占用、配置损坏。详见日志。",
            log_path,
        )

    # 终端里给完整地址（含 token）；日志里必须掩码——否则排错通道本身就是泄漏面
    url = _announce_urls(port, token, first_run=first_run, bind_host=bind_host)
    if log_path.is_file():
        logger.info("日志文件：%s", log_path)
    else:
        logger.warning("日志未落盘（%s 不可写），本次仅输出到终端", log_path)
    print("按 Ctrl+C 退出（网页里的「退出应用」同样有效）", flush=True)
    if open_browser:
        _open_browser(url)

    try:
        while not stop.wait(0.5):
            if not thread.is_alive():
                break
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        pass
    finally:
        _emergency_stop(server, stop)
        thread.join(timeout=5.0)
        _clear_token_file()
    return 0


def _emergency_stop(server: uvicorn.Server, stop: threading.Event) -> None:
    """幂等的收尾：置退出位并让 uvicorn 停（atexit 与正常退出都会走到）。"""
    stop.set()
    server.should_exit = True


def main(argv: list[str] | None = None) -> int:
    """Web 模式入口。

    Args:
        argv: 命令行参数（省略时读 sys.argv）。

    Returns:
        进程退出码。
    """
    args = _parse_args(argv)

    cm = ConfigManager()
    cm.load()
    log_level = str(cm.get("web.log_level", "INFO") or "INFO")
    log_file = Path(args.log_file).expanduser() if args.log_file else None
    written = configure_logging(
        level=log_level,
        log_file=log_file,
    )
    log_path = written or log_file or default_log_file()

    try:
        port = _resolve_port(args.port or int(cm.get("web.port", DEFAULT_PORT) or DEFAULT_PORT))

        # 监听地址与环境开关：参数 > 环境变量 > 配置 > 默认（保持只给本机）
        bind_host = (
            args.host
            or os.environ.get("WX_WEB_HOST", "").strip()
            or str(cm.get("web.host", "") or "").strip()
            or HOST
        )
        allow_remote = bool(
            args.allow_remote or _env_flag("WX_ALLOW_REMOTE") or cm.get("web.allow_remote", False)
        )
        if bind_host not in (HOST, "localhost"):
            logger.warning(
                "监听 %s（非回环）：服务不再只对本机开放，请确认这是你要的"
                "；来源放行开关 allow_remote=%s",
                bind_host,
                allow_remote,
            )
        if bind_host in (HOST, "localhost") and allow_remote:
            logger.info("已打开 allow_remote，但只绑回环，其它设备仍访问不到（需配 --host）")

        existing_url = _existing_instance_url(port, bind_host)
        if existing_url:
            if not _read_token_file():
                logger.warning("已有实例但读不到 token 文件 %s，浏览器可能无法通过鉴权", TOKEN_FILE)
            logger.info("%s 已在运行：%s", APP_NAME, mask_secrets(existing_url))
            print(f"{APP_NAME} 已在运行：{existing_url}", flush=True)
            if not args.no_open and bool(cm.get("web.open_browser", True)):
                _open_browser(existing_url)
            return 0

        if not _can_bind(port, bind_host):
            return _startup_failure(
                "wx-obsidian 端口被占用",
                f"{bind_host}:{port} 上不是 wx-obsidian 的实例，请先关闭占用它的程序，"
                f"或改用其它端口启动：--port 8766。",
                log_path,
            )

        return _serve(
            args,
            cm,
            bind_host=bind_host,
            port=port,
            log_path=log_path,
            allow_remote=allow_remote,
        )
    except Exception as exc:  # 无终端时不能让 traceback 消失在虚空里
        logger.exception("启动过程异常")
        print(
            f"启动异常：{type(exc).__name__}: {exc}（详见 {log_path}）", file=sys.stderr, flush=True
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
