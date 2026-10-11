"""Web 模式启动器：起本地服务 → 等就绪 → 打开浏览器 → 常驻。

刻意只绑 127.0.0.1；token 存 `~/.wx-obsidian/web.token`（0600），
供第二实例启动时复用（否则新开的标签页会因缺 token 而被拒）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import logging
import secrets
import signal
import sys
import threading
import time
import webbrowser
from types import FrameType

import requests
import uvicorn

from wx_obsidian.config import PROMPTS_DIR, SCRIPT_DIR, SKILLS_DIR
from wx_obsidian.config_manager import CONFIG_DIR
from wx_obsidian.web import APP_NAME, APP_VERSION
from wx_obsidian.web.api import STATIC_DIR, create_app
from wx_obsidian.web.runtime import build_runtime

logger = logging.getLogger(__name__)

HOST = "127.0.0.1"
TOKEN_FILE = CONFIG_DIR / "web.token"
READY_TIMEOUT_SECONDS = 10.0

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


def _wait_ready(port: int, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _ping(port):
            return True
        time.sleep(0.2)
    return False


def _build_index_url(port: int, token: str) -> str:
    return f"http://{HOST}:{port}/?token={token}"


def _resolve_port(configured: int) -> int:
    return configured if configured > 0 else 8765


def main(argv: list[str] | None = None) -> int:
    """Web 模式入口。

    Args:
        argv: 命令行参数（省略时读 sys.argv）。

    Returns:
        进程退出码。
    """
    args = _parse_args(argv)
    if args.check_paths:
        return _check_paths()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    runtime = build_runtime()
    configured_port = int(runtime.config_manager.get("web.port", 8765) or 8765)
    port = _resolve_port(args.port or configured_port)

    if _ping(port):
        existing_token = _read_token_file()
        url = f"http://{HOST}:{port}/"
        if existing_token:
            url = _build_index_url(port, existing_token)
        print(f"{APP_NAME} 已在运行：{url}", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        return 0

    token = secrets.token_urlsafe(24)
    _write_token_file(token)

    app = create_app(runtime, token=token)
    log_level = str(runtime.config_manager.get("web.log_level", "info") or "info").lower()
    server = uvicorn.Server(
        uvicorn.Config(app, host=HOST, port=port, log_level=log_level, access_log=False)
    )

    thread = threading.Thread(target=server.run, name="wx-obsidian-uvicorn", daemon=True)
    thread.start()

    if not _wait_ready(port):
        print(f"服务启动失败或超时（端口 {port} 可能被其它程序占用）", file=sys.stderr)
        server.should_exit = True
        _clear_token_file()
        return 1

    url = _build_index_url(port, token)
    # flush：输出重定向到文件/管道时，这行是最关键的（含 token 的访问地址）
    print(f"{APP_NAME} v{APP_VERSION} 已启动：{url}", flush=True)
    print("按 Ctrl+C 退出", flush=True)
    if not args.no_open:
        webbrowser.open(url)

    stop = threading.Event()

    def _handle_signal(signum: int, frame: FrameType | None) -> None:
        logger.info("收到信号 %s，准备退出", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    try:
        while not stop.wait(0.5):
            if not thread.is_alive():
                break
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        pass
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        _clear_token_file()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
