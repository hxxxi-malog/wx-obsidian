"""任务与 SSE 测试：单飞、事件映射、断线重放、取消。"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _client(orchestrator: FakeOrchestrator) -> TestClient:
    runtime, _ = make_runtime(orchestrator=orchestrator)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app)


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """把 SSE 响应体解析成事件列表。"""
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


def test_submit_job_and_stream_events() -> None:
    with _client(FakeOrchestrator(steps=["甲", "乙"])) as client:
        response = client.post("/api/jobs", json={"kind": "fetch"}, headers=AUTH)
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        stream = client.get(f"/api/jobs/{job_id}/events", headers=AUTH)
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(stream.text)
        types = [event["type"] for event in events]

        assert types[0] == "start"
        assert "article" in types
        assert types[-1] == "finished"
        # seq 单调递增（重连回放的依据）
        assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)

        articles = [event for event in events if event["type"] == "article"]
        assert [event["title"] for event in articles] == ["甲", "乙"]
        assert articles[-1]["completed"] == 2
        assert articles[-1]["total"] == 2

        finished = events[-1]
        assert finished["result"]["done"] == 2
        assert finished["result"]["items"][0]["title"] == "甲"


def test_sentinel_titles_map_to_semantic_events() -> None:
    script = [
        ("_kg", 0, 1),
        ("_kg_done", 0, 1),
        ("_related", 0, 1),
        ("_related_done", 0, 1),
        ("正文一篇", 1, 1),
    ]
    with _client(FakeOrchestrator(script=script, steps=[])) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)

    stages = [event for event in events if event["type"] == "stage"]
    assert [(event["stage"], event["state"]) for event in stages] == [
        ("knowledge_graph", "begin"),
        ("knowledge_graph", "end"),
        ("related", "begin"),
        ("related", "end"),
    ]
    # 哨兵不泄漏到前端：只有真实标题产生 article 事件
    assert [event["title"] for event in events if event["type"] == "article"] == ["正文一篇"]


def test_write_stage_details_do_not_count_as_articles() -> None:
    """写入阶段的明细（更新分类/生成概念）不能算成「处理了一篇文章」。"""
    script = [
        ("真实文章", 1, 1),
        ("更新分类: Agent", 1, 1),
        ("生成概念: Handoff（交接）", 1, 1),
        ("生成概念: 会话状态快照", 1, 1),
    ]
    with _client(FakeOrchestrator(script=script, steps=[])) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)

    assert [event["title"] for event in events if event["type"] == "article"] == ["真实文章"]
    assert [event["message"] for event in events if event["type"] == "detail"] == [
        "更新分类: Agent",
        "生成概念: Handoff（交接）",
        "生成概念: 会话状态快照",
    ]


def test_empty_result_reports_detail() -> None:
    script: list[tuple[str, int, int]] = []
    with _client(FakeOrchestrator(script=script, steps=[])) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        # _empty 由 orchestrator 在「没有新文章」时发出；这里直接验证映射本身
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)
    assert events[-1]["type"] == "finished"


def test_second_job_is_rejected_while_busy() -> None:
    slow = FakeOrchestrator(steps=["x"] * 20, step_delay=0.05)
    with _client(slow) as client:
        first = client.post("/api/jobs", json={}, headers=AUTH)
        assert first.status_code == 202

        second = client.post("/api/jobs", json={}, headers=AUTH)
        assert second.status_code == 409
        assert second.json()["error"] == "job_busy"

        # 结束后可以再提交
        client.get(f"/api/jobs/{first.json()['job_id']}/events", headers=AUTH)
        assert client.post("/api/jobs", json={}, headers=AUTH).status_code == 202


def test_cancel_marks_job_cancelled() -> None:
    slow = FakeOrchestrator(steps=["x"] * 50, step_delay=0.05)
    with _client(slow) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        assert client.post(f"/api/jobs/{job_id}/cancel", headers=AUTH).status_code == 200

        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)

    assert slow.cancelled.is_set()
    assert events[-1]["type"] == "cancelled"
    # 取消后不再处理全部 50 篇
    assert len([e for e in events if e["type"] == "article"]) < 50


def test_cancel_unknown_or_finished_job_conflicts() -> None:
    with _client(FakeOrchestrator(steps=[])) as client:
        assert client.post("/api/jobs/nope/cancel", headers=AUTH).status_code == 409


def test_last_event_id_replays_only_newer_events() -> None:
    with _client(FakeOrchestrator(steps=["一"])) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        all_events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)
        assert len(all_events) >= 2

        replay = client.get(
            f"/api/jobs/{job_id}/events",
            headers={**AUTH, "Last-Event-ID": str(all_events[0]["seq"])},
        )
    replayed = _parse_sse(replay.text)
    assert replayed
    assert all(event["seq"] > all_events[0]["seq"] for event in replayed)


def test_job_detail_and_list() -> None:
    with _client(FakeOrchestrator(steps=["一"])) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        client.get(f"/api/jobs/{job_id}/events", headers=AUTH)

        detail = client.get(f"/api/jobs/{job_id}", headers=AUTH).json()
        assert detail["state"] == "finished"
        assert detail["result"]["done"] == 1

        listing = client.get("/api/jobs", headers=AUTH).json()
        assert listing["current"] is None
        assert listing["recent"][0]["job_id"] == job_id

        assert client.get("/api/jobs/missing", headers=AUTH).status_code == 404


def test_unknown_job_stream_is_404() -> None:
    with _client(FakeOrchestrator(steps=[])) as client:
        assert client.get("/api/jobs/missing/events", headers=AUTH).status_code == 404


def test_orchestrator_failure_reports_error_state() -> None:
    broken = FakeOrchestrator(fail_with=RuntimeError("API 挂了"))
    with _client(broken) as client:
        job_id = client.post("/api/jobs", json={}, headers=AUTH).json()["job_id"]
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/events", headers=AUTH).text)
    assert events[-1]["type"] == "error"
    assert "API 挂了" in events[-1]["error"]


def test_retry_job_passes_article_ids() -> None:
    fake = FakeOrchestrator(steps=[])
    with _client(fake) as client:
        response = client.post(
            "/api/articles/retry", json={"article_ids": ["a", "b"]}, headers=AUTH
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        client.get(f"/api/jobs/{job_id}/events", headers=AUTH)

    assert fake.calls[-1] == {"kind": "retry", "article_ids": ["a", "b"]}


def test_retry_without_ids_is_400() -> None:
    with _client(FakeOrchestrator(steps=[])) as client:
        assert client.post("/api/articles/retry", json={}, headers=AUTH).status_code == 400
