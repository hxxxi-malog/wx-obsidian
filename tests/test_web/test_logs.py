"""运行日志测试：内存缓冲、接口读取、以及推给正在运行的任务。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.web import logs as logs_api
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _client(orchestrator: FakeOrchestrator | None = None) -> TestClient:
    runtime, _ = make_runtime(orchestrator=orchestrator or FakeOrchestrator(steps=[]))
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app)


def _parse_sse(body: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in body.strip().split("\n\n"):
        if not block or block.startswith(":"):
            continue
        data = ""
        for line in block.splitlines():
            if line.startswith("data:"):
                data = line[len("data:") :].strip()
        if data:
            events.append(json.loads(data))
    return events


def test_buffer_keeps_only_warning_and_above() -> None:
    buffer = logs_api.LogBuffer(capacity=3)
    logging.getLogger().addHandler(buffer)
    try:
        for index in range(5):
            logging.getLogger("wx_obsidian.test").warning("告警 %d", index)
        logging.getLogger("wx_obsidian.test").info("这条不该进缓冲")
    finally:
        logging.getLogger().removeHandler(buffer)

    entries = buffer.entries_since(0)
    assert [entry.message for entry in entries] == ["告警 2", "告警 3", "告警 4"]  # 环形容量 3
    assert all(entry.level == "WARNING" for entry in entries)
    assert buffer.last_seq == 5
    assert [entry.seq for entry in buffer.entries_since(4)] == [5]


def test_api_logs_returns_entries_after_seq() -> None:
    with _client() as client:
        first = client.get("/api/logs", headers=AUTH).json()
        base = first["last_seq"]

        logging.getLogger("wx_obsidian.test").warning("管线告警：图片下载失败")
        logging.getLogger("wx_obsidian.test").error("管线错误：API 返回 500")

        payload = client.get(f"/api/logs?since={base}", headers=AUTH).json()

    messages = [entry["message"] for entry in payload["entries"]]
    assert "管线告警：图片下载失败" in messages
    assert "管线错误：API 返回 500" in messages
    assert [entry["level"] for entry in payload["entries"]] == ["WARNING", "ERROR"]
    assert payload["last_seq"] > base


def test_logs_are_pushed_into_running_job_stream() -> None:
    """任务运行期间的告警要以 log 事件出现在进度流里（终端关了也能看到）。"""
    slow = FakeOrchestrator(steps=["x"] * 20, step_delay=0.05)
    with _client(slow) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        logging.getLogger("wx_obsidian.test").warning("处理第 3 篇时图片下载失败")

        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)

    logs = [event for event in events if event["type"] == "log"]
    assert any("图片下载失败" in (event.get("message") or "") for event in logs)
    assert any(event.get("level") == "WARNING" for event in logs)


def test_logs_without_running_job_do_not_leak_into_stream() -> None:
    """没有任务在跑时，告警只进缓冲，不应造成任何事件。"""
    with _client() as client:
        logging.getLogger("wx_obsidian.test").warning("空闲期的告警")
        listing = client.get("/api/jobs", headers=AUTH).json()
        payload = client.get("/api/logs", headers=AUTH).json()

    assert listing["current"] is None
    assert any("空闲期的告警" in entry["message"] for entry in payload["entries"])


def test_log_buffer_is_installed_once_per_app(tmp_path: Path) -> None:
    """重复建 app 不应把同一条日志记多份。"""
    with _client() as client:
        logging.getLogger("wx_obsidian.test").warning("只应出现一次")
        payload = client.get("/api/logs", headers=AUTH).json()

    hits = [entry for entry in payload["entries"] if entry["message"] == "只应出现一次"]
    assert len(hits) == 1
