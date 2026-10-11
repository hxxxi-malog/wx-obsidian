"""定时任务接口测试：增删、跨重启恢复、立即执行、参数校验。"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.web import scheduling
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _make_app(
    tmp_path: Path, orchestrator: FakeOrchestrator
) -> tuple[TestClient, ConfigManager, Path]:
    cfg_dir = tmp_path / "cfg"
    cm = ConfigManager(config_dir=cfg_dir)
    cm.load()
    runtime, _ = make_runtime(orchestrator=orchestrator, config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app), cm, cfg_dir


def test_jobs_start_empty(tmp_path: Path) -> None:
    with _make_app(tmp_path, FakeOrchestrator())[0] as client:
        payload = client.get("/api/scheduler/jobs", headers=AUTH).json()
    assert payload["jobs"] == []
    assert payload["state"]["fetch_hours"] == 0
    assert payload["state"]["allowed_hours"] == [1, 2, 6, 12, 24]


def test_add_fetch_job_persists_and_lists(tmp_path: Path) -> None:
    client, cm, cfg_dir = _make_app(tmp_path, FakeOrchestrator())
    with client:
        response = client.post(
            "/api/scheduler/jobs", json={"kind": "fetch", "interval": "2h"}, headers=AUTH
        )
        assert response.status_code == 200
        body = response.json()
        assert body["state"]["fetch_hours"] == 2
        assert [job["job_id"] for job in body["jobs"]] == [scheduling.FETCH_JOB_ID]

        listed = client.get("/api/scheduler/jobs", headers=AUTH).json()
        assert listed["jobs"][0]["next_run"] is not None

    # 设定写进了 config.json，重启后能恢复
    assert cm.get(scheduling.CONFIG_FETCH_INTERVAL) == 2
    reloaded = ConfigManager(config_dir=cfg_dir)
    reloaded.load()
    assert reloaded.get(scheduling.CONFIG_FETCH_INTERVAL) == 2


def test_add_keepalive_job(tmp_path: Path) -> None:
    client, cm, _ = _make_app(tmp_path, FakeOrchestrator())
    with client:
        response = client.post(
            "/api/scheduler/jobs", json={"kind": "keepalive", "enabled": True}, headers=AUTH
        )
        assert response.status_code == 200
        assert response.json()["state"]["keepalive_enabled"] is True

    assert cm.get(scheduling.CONFIG_KEEPALIVE_ENABLED) is True


def test_delete_job_clears_config(tmp_path: Path) -> None:
    """删任务必须同时清掉配置，否则重启后任务会「自己回来」。"""
    client, cm, _ = _make_app(tmp_path, FakeOrchestrator())
    with client:
        client.post("/api/scheduler/jobs", json={"kind": "fetch", "interval": "6h"}, headers=AUTH)
        response = client.delete(f"/api/scheduler/jobs/{scheduling.FETCH_JOB_ID}", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["jobs"] == []

    assert cm.get(scheduling.CONFIG_FETCH_INTERVAL) == 0


def test_invalid_interval_is_rejected(tmp_path: Path) -> None:
    with _make_app(tmp_path, FakeOrchestrator())[0] as client:
        for bad in ("3h", "abc"):
            response = client.post(
                "/api/scheduler/jobs", json={"kind": "fetch", "interval": bad}, headers=AUTH
            )
            assert response.status_code == 400, bad


def test_unknown_kind_and_job_are_rejected(tmp_path: Path) -> None:
    with _make_app(tmp_path, FakeOrchestrator())[0] as client:
        assert (
            client.post("/api/scheduler/jobs", json={"kind": "nope"}, headers=AUTH).status_code
            == 400
        )
        assert client.delete("/api/scheduler/jobs/nope", headers=AUTH).status_code == 404
        assert client.post("/api/scheduler/jobs/nope/run-now", headers=AUTH).status_code == 404


def test_schedule_survives_restart(tmp_path: Path) -> None:
    """同一个 config 目录「重启」后，定时抓取要自动恢复。"""
    client, _cm, _cfg = _make_app(tmp_path, FakeOrchestrator())
    with client:
        client.post("/api/scheduler/jobs", json={"kind": "fetch", "interval": "12h"}, headers=AUTH)

    # 第二次构造 = 重启
    restarted, _cm2, _cfg2 = _make_app(tmp_path, FakeOrchestrator())
    with restarted as client2:
        payload = client2.get("/api/scheduler/jobs", headers=AUTH).json()
    assert payload["state"]["fetch_hours"] == 12
    assert [job["job_id"] for job in payload["jobs"]] == [scheduling.FETCH_JOB_ID]


def test_run_now_submits_a_visible_job(tmp_path: Path) -> None:
    fake = FakeOrchestrator(steps=["一篇"])
    client, _, _ = _make_app(tmp_path, fake)
    with client:
        client.post("/api/scheduler/jobs", json={"kind": "fetch", "interval": "1h"}, headers=AUTH)
        response = client.post(
            f"/api/scheduler/jobs/{scheduling.FETCH_JOB_ID}/run-now", headers=AUTH
        )
        assert response.status_code == 200
        job_id = response.json()["job_id"]

        # 立即执行走 JobManager，所以任务能在页面上看到
        deadline = time.monotonic() + 5
        detail: dict = {}
        while time.monotonic() < deadline:
            detail = client.get(f"/api/jobs/{job_id}", headers=AUTH).json()
            if detail["state"] != "running":
                break
            time.sleep(0.05)

    assert detail["state"] == "finished"
    assert detail["result"]["done"] == 1


def test_run_now_keepalive_refreshes_cookie(tmp_path: Path) -> None:
    fake = FakeOrchestrator()
    client, _, _ = _make_app(tmp_path, fake)
    with client:
        client.post(
            "/api/scheduler/jobs", json={"kind": "keepalive", "enabled": True}, headers=AUTH
        )
        response = client.post(
            f"/api/scheduler/jobs/{scheduling.KEEPALIVE_JOB_ID}/run-now", headers=AUTH
        )
    assert response.status_code == 200
    assert fake.refreshed == 1


def test_scheduled_fetch_skips_when_busy() -> None:
    """定时抓取不能和手动抓取抢 processed.json：忙时直接跳过。"""
    import asyncio

    from wx_obsidian.web.jobs import JobManager

    async def scenario() -> None:
        fake = FakeOrchestrator(steps=["x"] * 10, step_delay=0.05)
        runtime, _ = make_runtime(orchestrator=fake)
        jobs = JobManager(runtime.orchestrator)

        await jobs.submit("fetch")
        first_id = jobs.current().job_id if jobs.current() else ""
        await scheduling._fetch_callable(jobs)()  # 模拟定时触发

        # 定时那次必须被单飞挡住：仍然是同一个任务，没有起第二个
        assert jobs.current() is not None
        assert jobs.current().job_id == first_id

        # 收尾：取消并等后台任务结束，避免留下悬挂任务
        jobs.cancel(first_id)
        deadline = time.monotonic() + 5
        while jobs.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

        assert len(fake.calls) == 1  # 全程只发起了一轮

    asyncio.run(scenario())
