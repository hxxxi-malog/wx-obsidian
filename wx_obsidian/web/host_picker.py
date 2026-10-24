# 宿主目录选择器：在 macOS 图形会话里弹出系统「选择文件夹」面板。
#
# 容器里的 Web 后端没有桌面连接，弹不出系统面板；由 scripts/host-picker.sh 把这个
# 服务装成 LaunchAgent（Aqua 会话）常驻，容器再经 host.docker.internal 转发请求。
# 端点文件与 HTTP 协议见 wx_obsidian/web/picker_bridge.py。
from __future__ import annotations

import argparse
import hmac
import json
import logging
import os
import secrets
import signal
import sys
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any
from urllib.parse import urlsplit

from wx_obsidian.web import fs as fs_api
from wx_obsidian.web import picker_bridge

logger = logging.getLogger("wx_obsidian.web.host_picker")

_DEFAULT_PROMPT = "选择 Obsidian Vault"
_MAX_BODY_BYTES = 4096


def _make_handler(token: str) -> type[BaseHTTPRequestHandler]:
    """绑定 token 的请求处理器（token 每次启动随机生成）。"""

    class _Handler(BaseHTTPRequestHandler):
        server_version = "wx-obsidian-picker"
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            # 配置页每次加载都会探测 /health，别让它把日志刷满
            if urlsplit(self.path).path == "/health":
                return
            logger.info("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:
            if urlsplit(self.path).path == "/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "platform": sys.platform})
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in ("/pick", "/cancel"):
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            if not self._authorized():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            if path == "/cancel":
                self._send_json(HTTPStatus.OK, {"ok": True, "cancelled": fs_api.cancel_pick()})
                return

            payload = self._read_json()
            prompt = str(payload.get("prompt") or _DEFAULT_PROMPT)
            try:
                chosen = fs_api.pick_directory(prompt)
            except fs_api.PickerBusyError as exc:
                self._send_json(HTTPStatus.CONFLICT, {"error": "picker_busy", "detail": str(exc)})
            except fs_api.PickerUnavailableError as exc:
                self._send_json(
                    HTTPStatus.NOT_IMPLEMENTED, {"error": "picker_unavailable", "detail": str(exc)}
                )
            except fs_api.PickerError as exc:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "picker_failed", "detail": str(exc)},
                )
            else:
                self._send_json(HTTPStatus.OK, {"path": chosen, "cancelled": not chosen})

        def _authorized(self) -> bool:
            provided = self.headers.get(picker_bridge.TOKEN_HEADER, "")
            return bool(provided) and hmac.compare_digest(provided, token)

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                return {}
            if length > _MAX_BODY_BYTES:
                # 不读超长请求体：关掉连接，避免把它留在缓冲里破坏下一个请求
                self.close_connection = True
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (UnicodeDecodeError, ValueError):
                return {}
            return payload if isinstance(payload, dict) else {}

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


def _write_endpoint(
    server: ThreadingHTTPServer, token: str, endpoint_path: Path, host: str
) -> picker_bridge.Endpoint:
    port = int(server.server_address[1])
    endpoint = picker_bridge.Endpoint(
        url=f"http://{host}:{port}",
        port=port,
        token=token,
        pid=os.getpid(),
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    picker_bridge.write_endpoint(endpoint, endpoint_path)
    return endpoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m wx_obsidian.web.host_picker",
        description="在 macOS 图形会话里弹系统目录面板，供容器里的 Web 后端转发调用。",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（保持回环）")
    parser.add_argument("--port", type=int, default=0, help="监听端口；0 表示随机")
    parser.add_argument(
        "--endpoint-file",
        type=Path,
        default=picker_bridge.ENDPOINT_FILE,
        help="端点文件路径（容器通过挂载目录读同一份）",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if not fs_api.native_platform():
        logger.error("宿主目录选择器仅支持 macOS（当前平台 %s）", sys.platform)
        return 1
    if not fs_api.picker_available():
        logger.error("找不到 osascript，无法弹出系统目录面板")
        return 1

    running, _detail = picker_bridge.bridge_state(args.endpoint_file)
    if running:
        logger.info("已经有宿主目录选择器在运行，本次启动直接退出")
        return 0

    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer((args.host, args.port), _make_handler(token))
    endpoint = _write_endpoint(server, token, args.endpoint_file, args.host)

    def _request_stop(_signum: int, _frame: FrameType | None) -> None:
        # shutdown() 要等 serve_forever 退出，只能在别的线程调用
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    logger.info("宿主目录选择器已启动：%s（端点文件 %s）", endpoint.url, args.endpoint_file)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        picker_bridge.remove_endpoint(args.endpoint_file)
        server.server_close()
        logger.info("宿主目录选择器已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
