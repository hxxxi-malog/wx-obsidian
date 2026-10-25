"""正文补抓：采集结束后的自动补抓与「最新文章」页的重试入口。

为什么单独一组测试：「无正文」有三个来源——采集阶段没取到、补抓队列还没轮到、
补抓失败。执行器与重试接口要把这三者分开表达，混成一种「空正文」界面就分不清
该等、该重试，还是该重新扫码。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import SourceArticle, SourceArticlePage
from wx_obsidian.sources.we_mp_rss import (
    CONTENT_STATE_FAILED,
    CONTENT_STATE_PENDING,
    CONTENT_STATE_READY,
)
from wx_obsidian.web import api as web_api
from wx_obsidian.web.content_backfill import ContentBackfillRunner

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}
WAIT_TIMEOUT = 5.0


def _article(article_id: str, content_state: str) -> SourceArticle:
    return SourceArticle(
        article_id=article_id,
        title=f"文章 {article_id}",
        url=f"https://mp.weixin.qq.com/s/{article_id}",
        account="阿里云云原生",
        account_id="MP_WXS_3537616032",
        published_at=datetime(2026, 10, 25, 8, 29, 40, tzinfo=UTC),
        has_content=content_state == CONTENT_STATE_READY,
        content_state=content_state,
        content_attempts=3 if content_state == CONTENT_STATE_FAILED else 0,
        upstream_id=f"3537616032-3537616032_{article_id}",
    )


def _page(*articles: SourceArticle) -> SourceArticlePage:
    return SourceArticlePage(total=len(articles), offset=0, limit=30, items=list(articles))


def _wait_done(runner: ContentBackfillRunner) -> dict[str, object]:
    """等一轮补抓结束（执行器自己起后台线程）。"""
    deadline = time.monotonic() + WAIT_TIMEOUT
    while time.monotonic() < deadline:
        snapshot = runner.snapshot()
        if not snapshot["backfill_running"]:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("正文补抓在超时内没有结束")


def _runner(fake: FakeOrchestrator, **overrides: Any) -> ContentBackfillRunner:
    options: dict[str, Any] = {
        "refresh_interval": 0.0,
        "pass_interval": 0.0,
        "min_passes": 1,
        "max_passes": 1,
    }
    options.update(overrides)
    return ContentBackfillRunner(fake, **options)


# -- 执行器 -----------------------------------------------------------------


def test_refreshes_only_pending_articles() -> None:
    """只补「待补抓」的：已有正文的不动，已失败的交给用户手动重试。"""
    fake = FakeOrchestrator(
        articles=_page(
            _article("ready-1", CONTENT_STATE_READY),
            _article("pending-1", CONTENT_STATE_PENDING),
            _article("failed-1", CONTENT_STATE_FAILED),
            _article("pending-2", CONTENT_STATE_PENDING),
        )
    )
    runner = _runner(fake)

    runner.start()
    snapshot = _wait_done(runner)

    assert fake.refreshed_articles == [
        "3537616032-3537616032_pending-1",
        "3537616032-3537616032_pending-2",
    ]
    assert snapshot["backfill_pending"] == 2
    assert snapshot["backfill_refreshed"] == 2
    assert snapshot["backfill_failed"] == 0
    assert snapshot["backfill_last_run_at"] != ""


def test_counts_refresh_failures() -> None:
    """上游拒绝补抓要记成失败篇数，界面才能提示「没补上，请重试」。"""
    fake = FakeOrchestrator(
        articles=_page(_article("pending-1", CONTENT_STATE_PENDING)),
        refresh_article_outcome="failed",
    )
    runner = _runner(fake)

    runner.start()
    snapshot = _wait_done(runner)

    assert snapshot["backfill_refreshed"] == 0
    assert snapshot["backfill_failed"] == 1


def test_second_start_is_skipped_while_running() -> None:
    """单飞：采集刚结束时手动再点一次，不能把同一批文章重复触发一遍。"""
    fake = FakeOrchestrator(
        articles=_page(
            _article("pending-1", CONTENT_STATE_PENDING),
            _article("pending-2", CONTENT_STATE_PENDING),
        )
    )
    runner = _runner(fake, refresh_interval=0.05)

    runner.start()
    duplicate = runner.start()
    assert duplicate["backfill_running"] is True

    _wait_done(runner)
    assert fake.refreshed_articles == [
        "3537616032-3537616032_pending-1",
        "3537616032-3537616032_pending-2",
    ]


def test_nothing_pending_finishes_immediately() -> None:
    """没有缺正文的文章时不触发任何刷新。"""
    fake = FakeOrchestrator(articles=_page(_article("ready-1", CONTENT_STATE_READY)))
    runner = _runner(fake)

    runner.start()
    snapshot = _wait_done(runner)

    assert fake.refreshed_articles == []
    assert snapshot["backfill_pending"] == 0


# -- Web 接口 ---------------------------------------------------------------


@pytest.fixture
def _fast_backfill(monkeypatch: pytest.MonkeyPatch) -> None:
    """把应用里的执行器换成「零等待」版本，测试不必真的等轮次间隔。"""
    monkeypatch.setattr(
        web_api,
        "ContentBackfillRunner",
        partial(
            ContentBackfillRunner,
            refresh_interval=0.0,
            pass_interval=0.0,
            min_passes=1,
            max_passes=1,
        ),
    )


def _client(
    config_dir: Path,
    **fake_options: Any,
) -> tuple[TestClient, FakeOrchestrator]:
    cm = ConfigManager(config_dir=config_dir)
    cm.load()
    cm.set("source.token", "upstream-token")
    runtime, fake = make_runtime(orchestrator=FakeOrchestrator(**fake_options), config_manager=cm)
    app = web_api.create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app, raise_server_exceptions=False), fake


@pytest.mark.usefixtures("_fast_backfill")
def test_backfill_endpoint_reports_progress(tmp_path: Path) -> None:
    """手动补抓入口返回进度字段，并真的把待补文章交给上游。"""
    client, fake = _client(
        tmp_path,
        articles=_page(_article("pending-1", CONTENT_STATE_PENDING)),
    )

    with client:
        payload = client.post("/api/source/backfill", headers=AUTH).json()
        assert set(payload) >= {
            "backfill_running",
            "backfill_pending",
            "backfill_refreshed",
            "backfill_failed",
            "backfill_last_run_at",
        }
        runner = client.app.state.backfill  # type: ignore[attr-defined]
        _wait_done(runner)

    assert fake.refreshed_articles == ["3537616032-3537616032_pending-1"]


@pytest.mark.usefixtures("_fast_backfill")
def test_sync_payload_carries_backfill_fields(tmp_path: Path) -> None:
    """采集状态接口带上补抓进度，界面才能显示「正在补充正文」。"""
    client, _ = _client(tmp_path)

    with client:
        payload = client.get("/api/source/sync", headers=AUTH).json()

    assert payload["backfill_running"] is False
    assert payload["backfill_pending"] == 0
    assert payload["backfill_refreshed"] == 0
    assert payload["backfill_failed"] == 0
    assert payload["backfill_last_run_at"] == ""


@pytest.mark.parametrize(
    ("outcome", "expected_ok"),
    [("triggered", True), ("skipped", True), ("failed", False)],
)
def test_refresh_article_endpoint_maps_outcome(
    tmp_path: Path, outcome: str, expected_ok: bool
) -> None:
    """重试按钮：已开始与已有任务都算成功（都在补），只有失败才提示错误。"""
    client, fake = _client(tmp_path, refresh_article_outcome=outcome)

    with client:
        payload = client.post("/api/source/articles/article-1/refresh", headers=AUTH).json()

    assert payload["ok"] is expected_ok
    assert payload["outcome"] == outcome
    assert fake.refreshed_articles == ["article-1"]


def test_refresh_article_requires_token(tmp_path: Path) -> None:
    """接口本身仍受我们自己的令牌保护。"""
    client, _ = _client(tmp_path)

    with client:
        response = client.post("/api/source/articles/article-1/refresh")

    assert response.status_code == 401
