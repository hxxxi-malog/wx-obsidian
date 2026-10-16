"""运行日志测试：内存缓冲、接口读取、SSE 日志流、以及推给正在运行的任务。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.web import logs as logs_api
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _make_app(orchestrator: FakeOrchestrator | None = None) -> FastAPI:
    runtime, _ = make_runtime(orchestrator=orchestrator or FakeOrchestrator(steps=[]))
    return create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))


def _client(orchestrator: FakeOrchestrator | None = None) -> TestClient:
    return TestClient(_make_app(orchestrator))


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


# --- N3 日志 SSE：历史回放、新增推送、断线续传、断开即结束 ------------------------------
#
# 这条流的结束条件只有「客户端断开」，而 starlette 的 TestClient 会把整个响应体缓冲到
# 流结束再返回（`handle_request` 里 `portal.call(self.app, ...)` 是阻塞的），所以这里
# 自己收发 ASGI 消息：读够帧后主动发 `http.disconnect`，并断言生成器在超时内收尾。


class _AsgiLogStream:
    """直连 ASGI app 读 `/api/logs/events` 的流式读取器。"""

    def __init__(
        self,
        app: Any,
        *,
        query: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._app = app
        self._query = query
        self._headers = headers or {}
        self._messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._disconnected = asyncio.Event()
        self._buffer = ""
        self._task: asyncio.Task[None] | None = None
        self.status = 0
        self.content_type = ""

    async def __aenter__(self) -> _AsgiLogStream:
        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/logs/events",
            "raw_path": b"/api/logs/events",
            "query_string": self._query.encode(),
            "root_path": "",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in self._headers.items()
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        self._task = asyncio.create_task(self._app(scope, self._receive, self._send))
        return self

    async def _receive(self) -> dict[str, Any]:
        # is_disconnected() 用「立刻取消的 CancelScope」探测 receive：只有不 await 的
        # 返回路径才会被它观察到，所以断开标志要在这里同步判断。
        if self._disconnected.is_set():
            return {"type": "http.disconnect"}
        await self._disconnected.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        await self._messages.put(message)

    async def next_frame(self, timeout: float = 5.0) -> str:
        """读一帧 SSE（读到空行为止）；超时直接失败，绝不永久阻塞。"""
        while "\n\n" not in self._buffer:
            try:
                message = await asyncio.wait_for(self._messages.get(), timeout)
            except TimeoutError as exc:
                raise AssertionError(f"{timeout}s 内没有收到新的 SSE 帧") from exc
            if message["type"] == "http.response.start":
                self.status = int(message["status"])
                for key, value in message.get("headers", []):
                    if key.decode().lower() == "content-type":
                        self.content_type = value.decode()
                continue
            if message["type"] != "http.response.body":
                continue
            self._buffer += message.get("body", b"").decode("utf-8")
            if not message.get("more_body", False):
                break
        frame, _, self._buffer = self._buffer.partition("\n\n")
        return frame

    async def aclose(self, timeout: float = 5.0) -> None:
        """主动断开并等待流结束（超时即断言失败，证明没有挂死）。"""
        self._disconnected.set()
        task, self._task = self._task, None
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout)
        except TimeoutError as exc:
            raise AssertionError(f"客户端断开后 SSE 流未在 {timeout}s 内结束") from exc

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()


def _frame(frame: str) -> dict[str, Any]:
    """解析一帧 SSE 成 {"id": int, "event": str, "data": dict}。"""
    parsed: dict[str, Any] = {"id": None, "event": "", "data": {}}
    for line in frame.splitlines():
        if line.startswith("id:"):
            parsed["id"] = int(line[len("id:") :].strip())
        elif line.startswith("event:"):
            parsed["event"] = line[len("event:") :].strip()
        elif line.startswith("data:"):
            parsed["data"] = json.loads(line[len("data:") :].strip())
    return parsed


def test_log_stream_replays_history_matching_api_logs() -> None:
    """历史帧按 seq 递增回放，data 与 GET /api/logs 的 entry 完全一致。"""
    app = _make_app()
    logger = logging.getLogger("wx_obsidian.test")
    for index in range(3):
        logger.warning("SSE 历史 %d", index)

    with TestClient(app) as api_client:
        expected = api_client.get("/api/logs", headers=AUTH).json()["entries"]

    async def scenario() -> list[dict[str, Any]]:
        async with _AsgiLogStream(app, headers=dict(AUTH)) as stream:
            frames = [_frame(await stream.next_frame()) for _ in range(len(expected))]
            assert stream.status == 200
            assert stream.content_type.startswith("text/event-stream")
            await stream.aclose()
        return frames

    frames = asyncio.run(scenario())

    assert len(frames) == len(expected) >= 3
    assert [frame["event"] for frame in frames] == ["log"] * len(frames)
    seqs = [frame["id"] for frame in frames]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert [frame["data"] for frame in frames] == expected


def test_log_stream_pushes_new_entries_while_open() -> None:
    """流开着时新增的日志也要被推过来。"""
    app = _make_app()
    logger = logging.getLogger("wx_obsidian.test")
    logger.warning("SSE 旧条目")

    received: list[dict[str, Any]] = []

    async def scenario() -> None:
        async with _AsgiLogStream(app, headers=dict(AUTH)) as stream:
            received.append(_frame(await stream.next_frame()))  # 历史
            logger.warning("SSE 新条目")  # 期间产生的日志
            received.append(_frame(await stream.next_frame()))  # 应被推送
            await stream.aclose()

    asyncio.run(scenario())

    assert [frame["data"]["message"] for frame in received] == ["SSE 旧条目", "SSE 新条目"]
    assert received[1]["id"] > received[0]["id"]
    assert received[1]["data"]["level"] == "WARNING"


def test_log_stream_resumes_from_last_event_id_or_since() -> None:
    """续传优先级：Last-Event-ID 头 > ?last_id= > ?since=。"""
    app = _make_app()
    logger = logging.getLogger("wx_obsidian.test")
    for index in range(3):
        logger.warning("SSE 续传 %d", index)

    entries = TestClient(app).get("/api/logs", headers=AUTH).json()["entries"]
    assert len(entries) == 3
    first, second, third = entries

    async def read_one(query: str = "", headers: dict[str, str] | None = None) -> dict[str, Any]:
        async with _AsgiLogStream(app, query=query, headers=headers) as stream:
            frame = _frame(await stream.next_frame())
            await stream.aclose()
        return frame

    # EventSource 自动重连会带 Last-Event-ID
    resumed = asyncio.run(read_one(headers={**AUTH, "Last-Event-ID": str(second["seq"])}))
    assert resumed["id"] == third["seq"]
    assert resumed["data"] == third

    # ?last_id= 是后备（SSE 的 EventSource 不能设自定义头，token 也只能走 query）
    resumed = asyncio.run(read_one(query=f"token={TOKEN}&last_id={first['seq']}"))
    assert resumed["id"] == second["seq"]

    # 两者都有 → 头优先
    resumed = asyncio.run(
        read_one(
            query=f"token={TOKEN}&last_id={first['seq']}",
            headers={**AUTH, "Last-Event-ID": str(second["seq"])},
        )
    )
    assert resumed["id"] == third["seq"]

    # 都没有 → 用 since
    resumed = asyncio.run(read_one(query=f"token={TOKEN}&since={first['seq']}"))
    assert resumed["id"] == second["seq"]

    # 非法值视为未提供：回落到 since，而不是整个请求失败
    resumed = asyncio.run(read_one(query=f"token={TOKEN}&since={first['seq']}&last_id=abc"))
    assert resumed["id"] == second["seq"]


def test_log_stream_terminates_on_disconnect_without_hanging() -> None:
    app = _make_app()
    logging.getLogger("wx_obsidian.test").warning("断开测试")

    async def scenario() -> None:
        async with _AsgiLogStream(app, headers=dict(AUTH)) as stream:
            await stream.next_frame()
            await stream.aclose(timeout=2.0)  # 断开后 2s 内必须收尾

    asyncio.run(scenario())
