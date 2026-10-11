"""T-18 文件系统接口：目录列举、macOS 原生目录选择面板、退出接口。"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_web.helpers import make_fake_runtime
from wx_obsidian.web import api as web_api
from wx_obsidian.web import fs as fs_api

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _client(**kwargs: Any) -> TestClient:
    app = web_api.create_app(
        make_fake_runtime(),
        token=TOKEN,
        extra_allowed_hosts=frozenset({"testclient"}),
        **kwargs,
    )
    return TestClient(app)


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


def test_list_dir_sorts_dirs_first(tmp_path: Path) -> None:
    (tmp_path / "zeta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "note.md").write_text("x", encoding="utf-8")

    payload = fs_api.list_dir(str(tmp_path))

    names = [entry["name"] for entry in payload["entries"]]
    assert names == ["alpha", "zeta", "note.md"]
    assert payload["path"] == str(tmp_path)
    assert payload["parent"] == str(tmp_path.parent)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", ValueError), ("relative/path", ValueError)],
)
def test_list_dir_rejects_bad_input(raw: str, expected: type[Exception]) -> None:
    with pytest.raises(expected):
        fs_api.list_dir(raw)


def test_list_dir_reports_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        fs_api.list_dir(str(tmp_path / "nope"))


# ---------------------------------------------------------------------------
# pick_directory（osascript）
# ---------------------------------------------------------------------------


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["osascript"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_pick_directory_unavailable_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api.sys, "platform", "linux")

    with pytest.raises(fs_api.PickerUnavailableError):
        fs_api.pick_directory()


def test_pick_directory_unavailable_without_osascript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api.sys, "platform", "darwin")
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: None)

    with pytest.raises(fs_api.PickerUnavailableError):
        fs_api.pick_directory()


def test_pick_directory_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`POSIX path of (choose folder)` 结尾带斜杠，必须归一化掉。"""
    monkeypatch.setattr(fs_api.sys, "platform", "darwin")
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: "/usr/bin/osascript")
    monkeypatch.setattr(
        fs_api.subprocess,
        "run",
        lambda *args, **kwargs: _completed(0, "/Users/malog/Documents/Vault/\n"),
    )

    assert fs_api.pick_directory() == "/Users/malog/Documents/Vault"


def test_pick_directory_returns_empty_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """取消不是错误：返回空串，由调用方原样保留旧值。"""
    monkeypatch.setattr(fs_api.sys, "platform", "darwin")
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: "/usr/bin/osascript")
    monkeypatch.setattr(
        fs_api.subprocess,
        "run",
        lambda *args, **kwargs: _completed(1, stderr="execution error: User canceled. (-128)"),
    )

    assert fs_api.pick_directory() == ""


def test_pick_directory_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api.sys, "platform", "darwin")
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: "/usr/bin/osascript")
    monkeypatch.setattr(
        fs_api.subprocess,
        "run",
        lambda *args, **kwargs: _completed(1, stderr="execution error: something broke"),
    )

    with pytest.raises(fs_api.PickerError):
        fs_api.pick_directory()


def test_pick_directory_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api.sys, "platform", "darwin")
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: "/usr/bin/osascript")

    def _timeout(*args: Any, **kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=1.0)

    monkeypatch.setattr(fs_api.subprocess, "run", _timeout)

    with pytest.raises(fs_api.PickerError):
        fs_api.pick_directory()


# ---------------------------------------------------------------------------
# POST /api/fs/pick
# ---------------------------------------------------------------------------


def test_fs_pick_returns_absolute_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "pick_directory", lambda prompt="": "/tmp/Vault")

    with _client() as client:
        response = client.post("/api/fs/pick", json={"prompt": "选择 Vault"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"path": "/tmp/Vault", "cancelled": False}


def test_fs_pick_marks_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "pick_directory", lambda prompt="": "")

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {"path": "", "cancelled": True}


def test_fs_pick_reports_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unavailable(prompt: str = "") -> str:
        raise fs_api.PickerUnavailableError("仅支持 macOS")

    monkeypatch.setattr(fs_api, "pick_directory", _unavailable)

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 501
    assert response.json()["error"] == "picker_unavailable"


def test_fs_pick_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken(prompt: str = "") -> str:
        raise fs_api.PickerError("osascript 挂了")

    monkeypatch.setattr(fs_api, "pick_directory", _broken)

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 500
    assert response.json()["error"] == "picker_failed"


def test_fs_pick_requires_token() -> None:
    with _client() as client:
        assert client.post("/api/fs/pick").status_code == 401


# ---------------------------------------------------------------------------
# POST /api/shutdown
# ---------------------------------------------------------------------------


def test_shutdown_not_supported_without_hook() -> None:
    """测试里没传 on_quit：如实报 501，而不是假装退出。"""
    with _client() as client:
        response = client.post("/api/shutdown", headers=AUTH)

    assert response.status_code == 501
    assert response.json()["error"] == "not_supported"


def test_shutdown_invokes_hook_after_responding() -> None:
    called: list[str] = []

    with _client(on_quit=lambda: called.append("quit")) as client:
        response = client.post("/api/shutdown", headers=AUTH)
        # 回调是延后触发的：先让响应送达浏览器，再停服务
        assert response.status_code == 200
        assert called == []
        for _ in range(40):
            if called:
                break
            time.sleep(0.05)

    assert called == ["quit"]


def test_shutdown_requires_token() -> None:
    with _client() as client:
        assert client.post("/api/shutdown").status_code == 401
