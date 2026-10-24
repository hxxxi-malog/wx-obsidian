"""宿主目录选择器的桥接层：端点文件与容器侧转发。"""

from __future__ import annotations

import contextlib
import json
import stat
import threading
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from wx_obsidian.web import picker_bridge

TOKEN = "picker-token"


@contextlib.contextmanager
def _stub_server(responses: dict[str, tuple[int, dict[str, Any]]]) -> Iterator[str]:
    """一个只认固定 token 的假宿主选择器。"""

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(HTTPStatus.OK, {"ok": True})
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.headers.get(picker_bridge.TOKEN_HEADER, "") != TOKEN:
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
                return
            status, payload = responses.get(self.path, (404, {"error": "not_found"}))
            self._send(status, payload)

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{int(server.server_address[1])}"
    finally:
        server.shutdown()
        server.server_close()


def _endpoint_file(tmp_path: Path, url: str) -> tuple[picker_bridge.Endpoint, Path]:
    endpoint = picker_bridge.Endpoint(
        url=url,
        port=int(url.rsplit(":", 1)[1]),
        token=TOKEN,
        pid=4242,
        started_at="2026-10-24T18:30:00+08:00",
    )
    path = tmp_path / "host-picker.json"
    picker_bridge.write_endpoint(endpoint, path)
    return endpoint, path


@pytest.fixture(autouse=True)
def _loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """客户端默认按容器判断地址；测试里走回环。"""
    monkeypatch.setattr(picker_bridge, "runs_in_container", lambda: False)


def test_endpoint_round_trip_is_private(tmp_path: Path) -> None:
    endpoint = picker_bridge.Endpoint(
        url="http://127.0.0.1:1234",
        port=1234,
        token="secret",
        pid=7,
        started_at="start",
    )
    path = picker_bridge.write_endpoint(endpoint, tmp_path / "host-picker.json")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert picker_bridge.read_endpoint(path) == endpoint


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        '{"url": "http://127.0.0.1:1", "token": "", "port": 1}',
        '{"url": "http://127.0.0.1:1", "token": "t", "port": 0}',
        '{"url": "", "token": "t", "port": 1}',
    ],
)
def test_read_endpoint_rejects_bad_payload(tmp_path: Path, content: str) -> None:
    path = tmp_path / "host-picker.json"
    path.write_text(content, encoding="utf-8")

    assert picker_bridge.read_endpoint(path) is None


def test_read_and_remove_missing_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "host-picker.json"

    assert picker_bridge.read_endpoint(path) is None
    picker_bridge.remove_endpoint(path)


def test_service_url_switches_to_host_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    endpoint = picker_bridge.Endpoint(url="http://127.0.0.1:4321", port=4321, token="t")

    assert picker_bridge.service_url(endpoint) == "http://127.0.0.1:4321"
    monkeypatch.setattr(picker_bridge, "runs_in_container", lambda: True)
    assert picker_bridge.service_url(endpoint) == "http://host.docker.internal:4321"


def test_pick_via_bridge_returns_path(tmp_path: Path) -> None:
    with _stub_server({"/pick": (200, {"path": "/Users/me/Vault", "cancelled": False})}) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        assert picker_bridge.pick_via_bridge("选择", path) == "/Users/me/Vault"


def test_pick_via_bridge_treats_cancel_as_empty(tmp_path: Path) -> None:
    with _stub_server({"/pick": (200, {"path": "", "cancelled": True})}) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        assert picker_bridge.pick_via_bridge("选择", path) == ""


def test_pick_via_bridge_maps_busy(tmp_path: Path) -> None:
    with _stub_server({"/pick": (409, {"error": "picker_busy", "detail": "面板已经打开"})}) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        with pytest.raises(picker_bridge.BridgeBusyError):
            picker_bridge.pick_via_bridge("选择", path)


def test_pick_via_bridge_maps_unavailable(tmp_path: Path) -> None:
    with _stub_server(
        {"/pick": (501, {"error": "picker_unavailable", "detail": "没有授权"})}
    ) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        with pytest.raises(picker_bridge.BridgeUnavailableError):
            picker_bridge.pick_via_bridge("选择", path)


def test_pick_via_bridge_maps_failure(tmp_path: Path) -> None:
    with _stub_server(
        {"/pick": (500, {"error": "picker_failed", "detail": "osascript 挂了"})}
    ) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        with pytest.raises(picker_bridge.BridgeError):
            picker_bridge.pick_via_bridge("选择", path)


def test_pick_via_bridge_without_endpoint_raises(tmp_path: Path) -> None:
    with pytest.raises(picker_bridge.BridgeUnavailableError) as excinfo:
        picker_bridge.pick_via_bridge("选择", tmp_path / "host-picker.json")

    assert "host-picker.sh" in str(excinfo.value)


def test_pick_via_bridge_connection_refused(tmp_path: Path) -> None:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    endpoint = picker_bridge.Endpoint(url=f"http://127.0.0.1:{port}", port=port, token=TOKEN)
    path = picker_bridge.write_endpoint(endpoint, tmp_path / "host-picker.json")

    with pytest.raises(picker_bridge.BridgeUnavailableError):
        picker_bridge.pick_via_bridge("选择", path)


def test_cancel_via_bridge(tmp_path: Path) -> None:
    with _stub_server({"/cancel": (200, {"ok": True, "cancelled": True})}) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        assert picker_bridge.cancel_via_bridge(path) is True


def test_bridge_state_reports_health(tmp_path: Path) -> None:
    with _stub_server({}) as url:
        _endpoint, path = _endpoint_file(tmp_path, url)

        assert picker_bridge.bridge_state(path) == (True, "")


def test_bridge_state_without_endpoint(tmp_path: Path) -> None:
    available, detail = picker_bridge.bridge_state(tmp_path / "host-picker.json")

    assert available is False
    assert "host-picker.sh install" in detail


def test_bridge_state_unreachable_endpoint(tmp_path: Path) -> None:
    endpoint = picker_bridge.Endpoint(url="http://127.0.0.1:1", port=1, token=TOKEN)
    path = picker_bridge.write_endpoint(endpoint, tmp_path / "host-picker.json")

    available, detail = picker_bridge.bridge_state(path)

    assert available is False
    assert "没有响应" in detail
