"""Web 模式启动器：起本地服务 → 等就绪 → 打开浏览器 → 常驻。

刻意只绑 127.0.0.1；token 存 `~/.wx-obsidian/web.token`（0600），
供第二实例启动时复用（否则新开的标签页会因缺 token 而被拒）。

三条与「双击即用」直接相关的设计：

- **单实例**（spec §4.6）：先 `GET /api/ping` 判断端口上是自己人还是别人。
  是自己人 → 只开浏览器后退出；是别人 → 立刻报错，而不是等 10 秒超时。
  端口占用本身就是进程级互斥，不需要锁文件。
- **日志必须落盘**（见 `web/logging_setup.py`）：`--windowed` 下没有终端，
  启动期失败若不写日志、不弹对话框，用户只会看到「双击了，什么都没发生」。
- **退出**：SIGINT/SIGTERM + `atexit` 都会优雅停；另外暴露 `POST /api/shutdown`
  供网页里的「退出应用」按钮调用——窗口化的 .app 没有 Cocoa 事件循环，
  系统菜单的 Cmd+Q 到不了本进程，必须给用户一个界面上的退出入口。
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import importlib
import json
import logging
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from types import FrameType

import requests
import uvicorn

from wx_obsidian.config import PROMPTS_DIR, SCRIPT_DIR, SKILLS_DIR
from wx_obsidian.config_manager import CONFIG_DIR, ConfigManager
from wx_obsidian.web import APP_NAME, APP_VERSION
from wx_obsidian.web.api import STATIC_DIR, create_app
from wx_obsidian.web.logging_setup import (
    configure_logging,
    default_log_file,
    mask_secrets,
    quiet_noisy_libraries,
)
from wx_obsidian.web.runtime import build_runtime

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
TOKEN_FILE = CONFIG_DIR / "web.token"
READY_TIMEOUT_SECONDS = 10.0
DEFAULT_PORT = 8765

# 打包自检用：这些依赖只要漏一个，冻结包都会在跑到对应功能时才炸
_CHECK_MODULES = (
    "fastapi",
    "uvicorn",
    "starlette",
    "requests",
    "yaml",
    "apscheduler",
    "jieba",
    "mistune",
)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m wx_obsidian.web", description="wx-obsidian Web 模式"
    )
    parser.add_argument("--port", type=int, default=0, help="监听端口（0=读配置，默认 8765）")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--log-file", default="", help="覆盖日志文件路径（排错用）")
    parser.add_argument(
        "--check-paths",
        action="store_true",
        help="只校验资源路径与依赖导入后退出（打包自检用）",
    )
    return parser.parse_args(argv)


def _check_paths() -> int:
    """校验冻结包里的资源路径与依赖导入，返回进程退出码。"""
    frozen = getattr(sys, "_MEIPASS", None)
    print(f"frozen      : {bool(frozen)}")
    if frozen:
        print(f"_MEIPASS    : {frozen}")
    print(f"script_dir  : {SCRIPT_DIR}")

    ok = True
    for label, path in (("skills", SKILLS_DIR), ("prompts", PROMPTS_DIR), ("static", STATIC_DIR)):
        exists = path.is_dir()
        ok = ok and exists
        print(f"{label:<12}: {path}  {'OK' if exists else 'MISSING'}")

    skill_files = (
        sorted(p.name for p in SKILLS_DIR.glob("*/SKILL.md")) if SKILLS_DIR.is_dir() else []
    )
    print(f"skill files : {len(skill_files)} {skill_files}")
    ok = ok and bool(skill_files)

    print("--- 依赖导入 ---")
    for name in _CHECK_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # 自检工具：任何导入失败都要报出来而不是崩掉
            ok = False
            print(f"{name:<12}: FAIL {type(exc).__name__}: {exc}")
        else:
            print(f"{name:<12}: OK")

    return 0 if ok else 1


def _is_frozen() -> bool:
    """是否跑在 PyInstaller 冻结包里。"""
    return bool(getattr(sys, "frozen", False))


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


def _ping(port: int) -> bool:
    """探测该端口上是否已有 wx-obsidian 实例。"""
    try:
        response = requests.get(f"http://{HOST}:{port}/api/ping", timeout=1.0)
    except requests.RequestException:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return isinstance(payload, dict) and payload.get("app") == APP_NAME


def _can_bind(port: int) -> bool:
    """端口能否被本进程绑定（与 uvicorn 一致地设置 SO_REUSEADDR，避免误报）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def _wait_ready(port: int, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ping(port):
            return True
        time.sleep(0.2)
    return False


def _build_url(port: int, token: str = "", *, first_run: bool = False) -> str:
    """拼访问地址。首启直接落到设置页，省掉「先去哪」的猜谜。"""
    path = "/settings" if first_run else "/"
    params: list[str] = []
    if token:
        params.append(f"token={token}")
    if first_run:
        params.append("first_run=1")
    query = "&".join(params)
    return f"http://{HOST}:{port}{path}" + (f"?{query}" if query else "")


def _resolve_port(configured: int) -> int:
    return configured if configured > 0 else DEFAULT_PORT


def _existing_instance_url(port: int) -> str:
    """已有实例的访问地址；没有实例时返回空串（单实例判定的唯一入口）。"""
    if not _ping(port):
        return ""
    return _build_url(port, _read_token_file())


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        logger.warning("无法自动打开浏览器，请手动访问上面的地址")


def _show_dialog(title: str, message: str) -> None:
    """冻结包启动失败时的兜底提示（无终端，只能靠系统对话框）。

    只在 macOS 冻结包里弹：源码运行时终端里已有完整输出，弹窗只会打扰开发。
    """
    if not (_is_frozen() and sys.platform == "darwin"):
        return
    script = (
        f'display dialog {json.dumps(message)} buttons {{"好"}} default button 1 '
        f"with title {json.dumps(title)} with icon caution"
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("无法弹出系统对话框", exc_info=True)


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


def _startup_failure(title: str, message: str, log_path: Path) -> int:
    logger.error("%s：%s", title, message)
    _show_dialog(title, message)
    print(f"{title}：{message}", file=sys.stderr, flush=True)
    print(f"日志：{log_path}", file=sys.stderr, flush=True)
    return 1


def _serve(args: argparse.Namespace, cm: ConfigManager, port: int, log_path: Path) -> int:
    """真正起服务的那条路径（单实例判定与端口检查已在调用方完成）。"""
    open_browser = bool(cm.get("web.open_browser", True)) and not args.no_open
    first_run = cm.is_first_run()

    token = secrets.token_urlsafe(24)
    _write_token_file(token)

    stop = threading.Event()
    _install_signal_handlers(stop)
    runtime = build_runtime(cm)
    quiet_noisy_libraries()
    app = create_app(runtime, token=token, on_quit=stop.set)
    log_level = str(cm.get("web.log_level", "info") or "info").lower()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
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

    if not _wait_ready(port):
        server.should_exit = True
        _clear_token_file()
        return _startup_failure(
            "wx-obsidian 启动失败",
            f"服务在 {READY_TIMEOUT_SECONDS:.0f} 秒内没有就绪（端口 {port}）。"
            f"常见原因：端口被占用、配置损坏。详见日志。",
            log_path,
        )

    url = _build_url(port, token, first_run=first_run)
    # 终端里给完整地址（含 token）；日志里必须掩码——否则排错通道本身就是泄漏面
    print(f"{APP_NAME} v{APP_VERSION} 已启动：{url}", flush=True)
    logger.info("%s v%s 已启动：%s", APP_NAME, APP_VERSION, mask_secrets(url))
    logger.info("日志文件：%s", log_path)
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
    if args.check_paths:
        # 自检要给人看，必须先于 stdout 重定向
        return _check_paths()

    cm = ConfigManager()
    cm.load()
    log_level = str(cm.get("web.log_level", "INFO") or "INFO")
    log_file = Path(args.log_file).expanduser() if args.log_file else None
    written = configure_logging(
        level=log_level,
        log_file=log_file,
        redirect_streams=_is_frozen(),
    )
    log_path = written or log_file or default_log_file()

    try:
        if _is_frozen() and written is None:
            _show_dialog(
                "wx-obsidian 无法写日志",
                "日志文件写入失败，应用仍会启动，但出错时将没有日志可查。",
            )
        port = _resolve_port(args.port or int(cm.get("web.port", DEFAULT_PORT) or DEFAULT_PORT))

        existing_url = _existing_instance_url(port)
        if existing_url:
            if not _read_token_file():
                logger.warning("已有实例但读不到 token 文件 %s，浏览器可能无法通过鉴权", TOKEN_FILE)
            logger.info("%s 已在运行：%s", APP_NAME, mask_secrets(existing_url))
            print(f"{APP_NAME} 已在运行：{existing_url}", flush=True)
            if not args.no_open and bool(cm.get("web.open_browser", True)):
                _open_browser(existing_url)
            return 0

        if not _can_bind(port):
            return _startup_failure(
                "wx-obsidian 端口被占用",
                f"端口 {port} 上不是 wx-obsidian 的实例，请先关闭占用它的程序，"
                f"或改用其它端口启动：--port 8766。",
                log_path,
            )

        return _serve(args, cm, port, log_path)
    except Exception as exc:  # 无终端时不能让 traceback 消失在虚空里
        logger.exception("启动过程异常")
        _show_dialog("wx-obsidian 启动异常", f"{type(exc).__name__}: {exc}\n详见日志 {log_path}")
        print(
            f"启动异常：{type(exc).__name__}: {exc}（详见 {log_path}）", file=sys.stderr, flush=True
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
