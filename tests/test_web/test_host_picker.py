"""宿主目录选择器的 HTTP 服务：鉴权、面板结果与错误映射。"""

from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from wx_obsidian.web import fs as fs_api
from wx_obsidian.web import host_picker, picker_bridge

TOKEN = "host-picker-token"


@contextlib.contextmanager
def _service() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), host_picker._make_handler(TOKEN))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{int(server.server_address[1])}"
    finally:
        server.shutdown()
        server.server_close()


def _call(
    base: str,
    path: str,
    *,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    token: str | None = TOKEN,
) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header(picker_bridge.TOKEN_HEADER, token)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_health_needs_no_token() -> None:
    with _service() as base:
        status, payload = _call(base, "/health", method="GET", token=None)

    assert status == 200
    assert payload["ok"] is True


def test_pick_requires_token() -> None:
    with _service() as base:
        status, _payload = _call(base, "/pick", token=None)

    assert status == 401


def test_pick_returns_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "pick_directory", lambda prompt="": str(tmp_path))
    with _service() as base:
        status, payload = _call(base, "/pick", payload={"prompt": "选择 Vault"})

    assert status == 200
    assert payload == {"path": str(tmp_path), "cancelled": False}


def test_pick_marks_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "pick_directory", lambda prompt="": "")
    with _service() as base:
        status, payload = _call(base, "/pick")

    assert status == 200
    assert payload == {"path": "", "cancelled": True}


def test_pick_reports_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _busy(prompt: str = "") -> str:
        raise fs_api.PickerBusyError("已经有一个面板打开")

    monkeypatch.setattr(fs_api, "pick_directory", _busy)
    with _service() as base:
        status, payload = _call(base, "/pick")

    assert status == 409
    assert payload["error"] == "picker_busy"


def test_pick_reports_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unavailable(prompt: str = "") -> str:
        raise fs_api.PickerUnavailableError("没有控制 System Events 的权限")

    monkeypatch.setattr(fs_api, "pick_directory", _unavailable)
    with _service() as base:
        status, payload = _call(base, "/pick")

    assert status == 501
    assert payload["error"] == "picker_unavailable"


def test_pick_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken(prompt: str = "") -> str:
        raise fs_api.PickerError("osascript 挂了")

    monkeypatch.setattr(fs_api, "pick_directory", _broken)
    with _service() as base:
        status, payload = _call(base, "/pick")

    assert status == 500
    assert payload["error"] == "picker_failed"


def test_cancel_reports_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "cancel_pick", lambda: True)
    with _service() as base:
        status, payload = _call(base, "/cancel")

    assert status == 200
    assert payload == {"ok": True, "cancelled": True}


def test_unknown_route_is_404() -> None:
    with _service() as base:
        status, _payload = _call(base, "/nope")

    assert status == 404


def test_main_rejects_non_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: False)

    assert host_picker.main([]) == 1
