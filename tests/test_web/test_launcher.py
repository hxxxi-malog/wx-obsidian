"""T-16 启动器测试：单实例判定、端口冲突、日志落盘与掩码。

这些测试刻意不启动真实管线：冻结包专属行为（落盘、弹窗）在 multi-line
场景下无法在单测里复现，改由 `packaging/smoke_frozen.py` 对真 .app 端到端验证；
这里覆盖的是「判定逻辑」本身。
"""

from __future__ import annotations

import contextlib
import http.server
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.web import __main__ as launcher
from wx_obsidian.web.logging_setup import configure_logging, mask_secrets, quiet_noisy_libraries

TOKEN = "test-token-123"


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """`configure_logging` 会往 root logger 挂 handler：用完必须摘掉，否则污染其它测试。"""
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            with contextlib.suppress(OSError):
                handler.close()
    root.setLevel(level)


# ---------------------------------------------------------------------------
# 日志与掩码
# ---------------------------------------------------------------------------


def test_mask_secrets_masks_key_token_and_bearer() -> None:
    text = "key=sk-abcdef123456 url=http://x/?token=SUPERSECRET auth=Bearer abc.def.ghi"
    masked = mask_secrets(text)

    assert "abcdef123456" not in masked
    assert "SUPERSECRET" not in masked
    assert "abc.def.ghi" not in masked
    assert "sk-****" in masked


def test_configure_logging_masks_secrets_in_file(tmp_path: Path) -> None:
    log_file = tmp_path / "logs" / "app.log"
    written = configure_logging(level="INFO", log_file=log_file)

    assert written == log_file
    logger = logging.getLogger("wx_obsidian.test")
    logger.warning("已启动：http://127.0.0.1:8765/?token=SUPERSECRET")
    logger.warning("llm key=sk-livekey123456")

    content = log_file.read_text(encoding="utf-8")
    assert "SUPERSECRET" not in content
    assert "sk-livekey123456" not in content
    assert "token=****" in content


def test_quiet_noisy_libraries_silences_jieba() -> None:
    """jieba 在 import 时把级别设回 DEBUG，必须在 import 之后再压一次。"""
    import jieba  # noqa: F401

    logging.getLogger("jieba").setLevel(logging.DEBUG)
    quiet_noisy_libraries()

    assert logging.getLogger("jieba").level == logging.WARNING


def test_configure_logging_survives_unusable_path(tmp_path: Path) -> None:
    """日志写不进去不能拦住启动：返回 None，由调用方降级。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")

    assert configure_logging(level="INFO", log_file=blocker / "app.log") is None


# ---------------------------------------------------------------------------
# 单实例判定与端口检查
# ---------------------------------------------------------------------------


class _PingHandler(http.server.BaseHTTPRequestHandler):
    payload: dict[str, str] = {"app": "wx-obsidian", "version": "0.1.0"}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定命名
        body = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """静音访问日志。"""


@contextlib.contextmanager
def _http_server(handler: type[http.server.BaseHTTPRequestHandler]) -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_existing_instance_url_reuses_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_file = tmp_path / "web.token"
    monkeypatch.setattr(launcher, "TOKEN_FILE", token_file)
    token_file.write_text("reuse-me", encoding="utf-8")

    with _http_server(_PingHandler) as port:
        url = launcher._existing_instance_url(port)

    assert url == f"http://127.0.0.1:{port}/?token=reuse-me"


def test_existing_instance_url_ignores_other_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """端口上是别的程序时不能被当成「已在运行」。"""

    class _OtherApp(_PingHandler):
        payload = {"app": "someone-else"}

    monkeypatch.setattr(launcher, "TOKEN_FILE", tmp_path / "web.token")
    with _http_server(_OtherApp) as port:
        assert launcher._existing_instance_url(port) == ""


def test_existing_instance_url_empty_when_port_closed() -> None:
    assert launcher._existing_instance_url(_free_port()) == ""


def test_can_bind_detects_conflict() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        assert launcher._can_bind(port) is False
    finally:
        sock.close()

    assert launcher._can_bind(_free_port()) is True


def test_build_url_variants() -> None:
    assert launcher._build_url(8765) == "http://127.0.0.1:8765/"
    assert launcher._build_url(8765, "abc") == "http://127.0.0.1:8765/?token=abc"
    assert (
        launcher._build_url(8765, "abc", first_run=True)
        == "http://127.0.0.1:8765/settings?token=abc&first_run=1"
    )
    assert launcher._build_url(9000, first_run=True) == "http://127.0.0.1:9000/settings?first_run=1"


def test_resolve_port_falls_back_to_default() -> None:
    assert launcher._resolve_port(0) == launcher.DEFAULT_PORT
    assert launcher._resolve_port(9001) == 9001


def test_show_dialog_is_noop_outside_frozen_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """源码运行时终端里已有输出，弹系统对话框只会打扰开发。"""

    def _explode(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("非冻结环境不应调用 osascript")

    monkeypatch.setattr(launcher.subprocess, "run", _explode)
    monkeypatch.setattr(launcher, "_is_frozen", lambda: False)

    launcher._show_dialog("标题", "内容")


# ---------------------------------------------------------------------------
# main() 集成：单实例 / 端口冲突 / 退出接口
# ---------------------------------------------------------------------------


class _StubConfigManager:
    """替代真实 ConfigManager：测试绝不能读用户真实的 ~/.wx-obsidian。"""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = values or {}

    def load(self) -> dict[str, Any]:
        return self.values

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def is_first_run(self) -> bool:
        return bool(self.values.get("first_run", False))


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    values: dict[str, Any] | None = None,
) -> int:
    monkeypatch.setattr(launcher, "ConfigManager", lambda: _StubConfigManager(values))
    return launcher.main([*argv, "--no-open", "--log-file", str(tmp_path / "app.log")])


def test_main_hands_off_to_existing_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """已有实例时只开浏览器并退出——不能再起第二个服务（spec §4.6）。"""
    token_file = tmp_path / "web.token"
    monkeypatch.setattr(launcher, "TOKEN_FILE", token_file)
    token_file.write_text("reuse-me", encoding="utf-8")

    with _http_server(_PingHandler) as port:
        started = time.monotonic()
        code = _run_main(monkeypatch, tmp_path, ["--port", str(port)])
        elapsed = time.monotonic() - started
        # 交接后 stub 实例仍在服务：说明启动器没有抢端口、也没有把它踢掉
        assert launcher._ping(port)

    assert code == 0
    # 走的是「立刻交接」而不是「等 10 秒就绪超时」
    assert elapsed < 5.0


def test_main_reports_port_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        code = _run_main(monkeypatch, tmp_path, ["--port", str(port)])
    finally:
        sock.close()

    assert code == 1
    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "端口" in content


def test_main_serves_then_shutdown_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真起一次 uvicorn：就绪 → 写 token 文件 → POST /api/shutdown → 干净退出。"""
    token_file = tmp_path / "web.token"
    monkeypatch.setattr(launcher, "TOKEN_FILE", token_file)
    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]))
    monkeypatch.setattr(launcher, "build_runtime", lambda _cm: runtime)

    port = _free_port()
    result: dict[str, Any] = {}

    def _run() -> None:
        result["code"] = launcher.main(
            ["--port", str(port), "--no-open", "--log-file", str(tmp_path / "app.log")]
        )

    monkeypatch.setattr(launcher, "ConfigManager", lambda: _StubConfigManager())
    thread = threading.Thread(target=_run, name="wx-launcher-test", daemon=True)
    thread.start()

    try:
        assert launcher._wait_ready(port, timeout=15.0), "服务未在 15 秒内就绪"
        token = token_file.read_text(encoding="utf-8").strip()
        assert token
        response = requests.post(
            f"http://127.0.0.1:{port}/api/shutdown",
            headers={"X-WX-Token": token},
            timeout=5.0,
        )
        assert response.status_code == 200
        thread.join(timeout=15.0)
        assert not thread.is_alive()
        assert result["code"] == 0
    finally:
        if thread.is_alive():  # 断言失败也要把服务收掉，免得残留进程占端口
            with contextlib.suppress(requests.RequestException):
                requests.post(
                    f"http://127.0.0.1:{port}/api/shutdown",
                    headers={"X-WX-Token": token_file.read_text(encoding="utf-8").strip()},
                    timeout=2.0,
                )
            thread.join(timeout=5.0)

    # 退出后不留 token 文件：下次启动重新生成
    assert not token_file.exists()

    # uvicorn 的日志必须经 root 落盘（log_config=None），而不是被 stderr 重定向
    # 记成 ERROR —— 否则真错误会被一排假的 [ERROR] 淹没。
    log_text = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "uvicorn.error" in log_text
    assert "[ERROR] wx_obsidian.stderr: INFO:" not in log_text


def test_failure_message_does_not_advertise_unwritable_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """日志写不进去时不能还让人去查一个不存在的文件。"""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    undefined_log = blocker / "app.log"

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = int(sock.getsockname()[1])
    try:
        monkeypatch.setattr(launcher, "ConfigManager", lambda: _StubConfigManager())
        code = launcher.main(["--port", str(port), "--no-open", "--log-file", str(undefined_log)])
    finally:
        sock.close()

    assert code == 1
    stderr = capsys.readouterr().err
    assert "日志未能落盘" in stderr
    assert f"日志：{undefined_log}" not in stderr
