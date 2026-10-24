"""T-18 文件系统接口：目录列举、原生目录选择面板（直接 / 宿主桥接）、退出接口。"""

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
from wx_obsidian.web import picker_bridge

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


@pytest.fixture(autouse=True)
def _reset_picker_state() -> Any:
    """picker 的模块级状态（锁、活动进程）不跨测试残留。"""
    yield
    fs_api._active_process = None
    fs_api._cancel_requested = False
    if fs_api._pick_lock.locked():
        fs_api._pick_lock.release()


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


class _FakeProcess:
    """subprocess.Popen 的替身：记录 communicate/terminate 调用。"""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
        timeout: bool = False,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timeout = timeout
        self.terminated = False
        self.communicate_calls: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.communicate_calls.append(timeout)
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd="osascript", timeout=timeout or 0)
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15


def _patch_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: "/usr/bin/osascript")


def _patch_popen(monkeypatch: pytest.MonkeyPatch, process: _FakeProcess) -> None:
    monkeypatch.setattr(fs_api.subprocess, "Popen", lambda *args, **kwargs: process)


def test_pick_script_hosts_dialog_in_system_events() -> None:
    """裸 choose folder 的面板会落到浏览器后面；必须交给 System Events 承载。"""
    script = "\n".join(fs_api.pick_script("选择 Vault"))

    assert 'tell application "System Events"' in script
    assert "with timeout of" in script
    assert "choose folder with prompt" in script
    assert script.startswith("osascript\n")


def test_pick_script_keeps_prompt_single_line() -> None:
    script = "\n".join(fs_api.pick_script('第一行\n第二行 "带引号"'))

    assert "第一行 第二行" in script
    assert '\\"带引号\\"' in script


def test_pick_directory_unavailable_off_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: False)

    with pytest.raises(fs_api.PickerUnavailableError):
        fs_api.pick_directory()


def test_pick_directory_unavailable_without_osascript(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
    monkeypatch.setattr(fs_api.shutil, "which", lambda _name: None)

    with pytest.raises(fs_api.PickerUnavailableError):
        fs_api.pick_directory()


def test_pick_directory_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    """`POSIX path of (choose folder)` 结尾带斜杠，必须归一化掉。"""
    _patch_macos(monkeypatch)
    _patch_popen(monkeypatch, _FakeProcess(stdout="/Users/malog/Documents/Vault/\n"))

    assert fs_api.pick_directory() == "/Users/malog/Documents/Vault"


def test_pick_directory_parses_root(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_macos(monkeypatch)
    _patch_popen(monkeypatch, _FakeProcess(stdout="/\n"))

    assert fs_api.pick_directory() == "/"


def test_pick_directory_returns_empty_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """取消不是错误：返回空串，由调用方原样保留旧值。"""
    _patch_macos(monkeypatch)
    _patch_popen(
        monkeypatch,
        _FakeProcess(returncode=1, stderr="execution error: User canceled. (-128)"),
    )

    assert fs_api.pick_directory() == ""


def test_pick_directory_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_macos(monkeypatch)
    _patch_popen(monkeypatch, _FakeProcess(returncode=1, stderr="execution error: something broke"))

    with pytest.raises(fs_api.PickerError):
        fs_api.pick_directory()


def test_pick_directory_reports_missing_automation_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_macos(monkeypatch)
    _patch_popen(
        monkeypatch,
        _FakeProcess(
            returncode=1,
            stderr="execution error: Not authorized to send Apple events to System Events. (-1743)",
        ),
    )

    with pytest.raises(fs_api.PickerUnavailableError) as excinfo:
        fs_api.pick_directory()
    assert "自动化" in str(excinfo.value)


def test_pick_directory_raises_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_macos(monkeypatch)
    process = _FakeProcess(timeout=True)
    _patch_popen(monkeypatch, process)
    closed: list[bool] = []
    monkeypatch.setattr(fs_api, "_close_dialog", lambda: closed.append(True))

    with pytest.raises(fs_api.PickerError):
        fs_api.pick_directory()

    assert process.terminated is True
    assert closed == [True]


def test_pick_directory_reports_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_macos(monkeypatch)
    assert fs_api._pick_lock.acquire(blocking=False)

    with pytest.raises(fs_api.PickerBusyError):
        fs_api.pick_directory()


def test_cancel_pick_terminates_active_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_macos(monkeypatch)
    process = _FakeProcess()
    fs_api._active_process = process
    closed: list[bool] = []
    monkeypatch.setattr(fs_api, "_close_dialog", lambda: closed.append(True))

    assert fs_api.cancel_pick() is True
    assert process.terminated is True
    assert closed == [True]

    fs_api._active_process = None
    assert fs_api.cancel_pick() is False


# ---------------------------------------------------------------------------
# POST /api/fs/pick
# ---------------------------------------------------------------------------


def _patch_direct_picker(monkeypatch: pytest.MonkeyPatch, result: str) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
    monkeypatch.setattr(fs_api, "pick_directory", lambda prompt="": result)


def _patch_bridge_picker(monkeypatch: pytest.MonkeyPatch, result: str) -> None:
    monkeypatch.setattr(fs_api, "native_platform", lambda: False)
    monkeypatch.setattr(picker_bridge, "pick_via_bridge", lambda prompt, endpoint_path=None: result)


def test_fs_pick_returns_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    (vault / "公众号文章").mkdir(parents=True)
    _patch_direct_picker(monkeypatch, str(vault))

    with _client() as client:
        response = client.post("/api/fs/pick", json={"prompt": "选择 Vault"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "path": str(vault),
        "cancelled": False,
        "visible": True,
        "writable": True,
        "detail": "",
    }


def test_fs_pick_marks_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_direct_picker(monkeypatch, "")

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "path": "",
        "cancelled": True,
        "visible": False,
        "writable": False,
        "detail": "",
    }


def test_fs_pick_reports_invisible_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """容器里看不到的目录不能假装选中成功。"""
    _patch_direct_picker(monkeypatch, str(tmp_path / "nope"))

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    payload = response.json()
    assert response.status_code == 200
    assert payload["visible"] is False
    assert payload["writable"] is False
    assert "看不到" in payload["detail"]


def test_fs_pick_reports_unwritable_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    articles = vault / "公众号文章"
    articles.mkdir(parents=True)
    articles.chmod(0o500)
    _patch_direct_picker(monkeypatch, str(vault))

    try:
        with _client() as client:
            response = client.post("/api/fs/pick", headers=AUTH)
    finally:
        articles.chmod(0o700)

    payload = response.json()
    assert payload["visible"] is True
    assert payload["writable"] is False
    assert "写不进" in payload["detail"]


def test_fs_pick_uses_host_bridge_off_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _patch_bridge_picker(monkeypatch, str(vault))

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    payload = response.json()
    assert response.status_code == 200
    assert payload["path"] == str(vault)
    assert payload["visible"] is True


def test_fs_pick_reports_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    def _unavailable(prompt: str = "") -> str:
        raise fs_api.PickerUnavailableError("仅支持 macOS")

    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
    monkeypatch.setattr(fs_api, "pick_directory", _unavailable)

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 501
    assert response.json()["error"] == "picker_unavailable"


def test_fs_pick_reports_bridge_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(prompt: str, endpoint_path: object = None) -> str:
        raise picker_bridge.BridgeUnavailableError("没有检测到宿主目录选择器")

    monkeypatch.setattr(fs_api, "native_platform", lambda: False)
    monkeypatch.setattr(picker_bridge, "pick_via_bridge", _missing)

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 501
    assert response.json()["error"] == "picker_unavailable"
    assert "宿主目录选择器" in response.json()["detail"]


def test_fs_pick_reports_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _busy(prompt: str = "") -> str:
        raise fs_api.PickerBusyError("已经打开了一个面板")

    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
    monkeypatch.setattr(fs_api, "pick_directory", _busy)

    with _client() as client:
        response = client.post("/api/fs/pick", headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"] == "picker_busy"


def test_fs_pick_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _broken(prompt: str = "") -> str:
        raise fs_api.PickerError("osascript 挂了")

    monkeypatch.setattr(fs_api, "native_platform", lambda: True)
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
