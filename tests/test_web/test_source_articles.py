"""GET /api/source/articles：数据源文章库分页接口。

为什么单独一组测试：这个接口要同时表达三种状态——正常分页、令牌失效、
数据源不可达。三者混成同一种「空列表」会让界面给出错误引导（让人去抓取，
而真正的问题是令牌）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import requests
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import SourceArticle, SourceArticlePage
from wx_obsidian.sources.base import SourceAuthError
from wx_obsidian.web import api as web_api

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


class UnexpectedError(Exception):
    """占位异常：不属于上游契约错误的意外失败。"""


def _article(article_id: str, title: str = "文章标题") -> SourceArticle:
    return SourceArticle(
        article_id=article_id,
        title=title,
        url=f"https://mp.weixin.qq.com/s/{article_id}",
        account="阿里云云原生",
        account_id="MP_WXS_3537616032",
        published_at=datetime(2026, 10, 23, 5, 6, 48, tzinfo=UTC),
        cover="https://mmbiz.qpic.cn/cover.jpg",
        has_content=True,
    )


def _page(*ids: str, total: int | None = None) -> SourceArticlePage:
    items = [_article(article_id) for article_id in ids]
    return SourceArticlePage(
        total=len(items) if total is None else total,
        offset=0,
        limit=30,
        items=items,
    )


def _client(
    config_dir: Path,
    *,
    articles: SourceArticlePage | None = None,
    articles_error: Exception | None = None,
    source_token: str = "upstream-token",
) -> tuple[TestClient, FakeOrchestrator]:
    cm = ConfigManager(config_dir=config_dir)
    cm.load()
    cm.set("source.token", source_token)
    runtime, fake = make_runtime(
        orchestrator=FakeOrchestrator(articles=articles, articles_error=articles_error),
        config_manager=cm,
    )
    app = web_api.create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    # 意外异常要能被断言成 500，而不是由 TestClient 直接抛出来
    return TestClient(app, raise_server_exceptions=False), fake


@pytest.fixture(autouse=True)
def _no_processed(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认账本为空：processed 标记的用例自己注入。"""
    monkeypatch.setattr(web_api, "load_processed", lambda: {})


def test_returns_page_with_processed_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """已入库的文章要带 processed=True，链接与时间原样透出。"""
    monkeypatch.setattr(web_api, "load_processed", lambda: {"known-token": {"title": "旧文章"}})
    client, fake = _client(tmp_path, articles=_page("known-token", "fresh-token", total=54))

    with client:
        response = client.get("/api/source/articles?offset=0&limit=2", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 54
    assert payload["offset"] == 0
    assert payload["limit"] == 2
    assert payload["token_configured"] is True
    assert [item["processed"] for item in payload["items"]] == [True, False]
    first = payload["items"][0]
    assert first["article_id"] == "known-token"
    assert first["url"] == "https://mp.weixin.qq.com/s/known-token"
    assert first["published_at"].startswith("2026-10-23T05:06:48")
    assert first["account"] == "阿里云云原生"
    assert first["has_content"] is True
    assert fake.article_queries == [{"offset": 0, "limit": 2, "feed_id": ""}]


def test_passes_feed_filter_and_pagination(tmp_path: Path) -> None:
    """公众号过滤与分页参数要透传到数据源。"""
    client, fake = _client(tmp_path, articles=_page("a", "b"))

    with client:
        response = client.get(
            "/api/source/articles?offset=30&limit=15&mp_id=MP_WXS_3537616032", headers=AUTH
        )

    assert response.status_code == 200
    assert fake.article_queries == [{"offset": 30, "limit": 15, "feed_id": "MP_WXS_3537616032"}]


def test_token_invalid_is_reported_without_500(tmp_path: Path) -> None:
    """上游 401 要变成可引导的错误码，而不是 500。"""
    client, _ = _client(
        tmp_path,
        articles_error=SourceAuthError("数据源访问令牌未配置或已失效（上游返回 401/403）"),
        source_token="",
    )

    with client:
        response = client.get("/api/source/articles", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] == "source_token_invalid"
    assert payload["token_configured"] is False
    assert "令牌" in payload["detail"]


def test_source_unavailable_is_reported_without_500(tmp_path: Path) -> None:
    """数据源不可达时给可重试的错误码。"""
    client, _ = _client(tmp_path, articles_error=requests.ConnectionError("connection refused"))

    with client:
        response = client.get("/api/source/articles", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["error"] == "source_unavailable"
    assert payload["token_configured"] is True
    assert "ConnectionError" in payload["detail"]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_rejects_out_of_range_pagination(tmp_path: Path, query: str) -> None:
    """越界分页参数由 FastAPI 校验直接拒绝，不下发到上游。"""
    client, fake = _client(tmp_path, articles=_page("a"))

    with client:
        response = client.get(f"/api/source/articles?{query}", headers=AUTH)

    assert response.status_code == 422
    assert fake.article_queries == []


def test_requires_wx_token(tmp_path: Path) -> None:
    """接口本身仍受我们自己的令牌保护。"""
    client, _ = _client(tmp_path, articles=_page("a"))

    with client:
        response = client.get("/api/source/articles")

    assert response.status_code == 401
    assert response.json() == {"error": "unauthorized"}


def test_unknown_error_is_not_swallowed(tmp_path: Path) -> None:
    """意外错误照常 500：界面上出现「服务异常」比静默空列表诚实。"""
    client, _ = _client(tmp_path, articles_error=UnexpectedError("boom"))

    with client:
        response = client.get("/api/source/articles", headers=AUTH)

    assert response.status_code == 500


def test_processed_uses_normalized_article_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """processed 判定用归一化后的短链 token（与 processed.json 的键一致）。"""
    processed: dict[str, Any] = {"wpHU8~UqRh-D-XYfzfZe1Q": {"title": "已处理"}}
    monkeypatch.setattr(web_api, "load_processed", lambda: processed)
    item = SourceArticle(
        article_id="wpHU8~UqRh-D-XYfzfZe1Q",
        title="文章",
        url="https://mp.weixin.qq.com/s/wpHU8~UqRh-D-XYfzfZe1Q",
        account="某号",
        account_id="MP_WXS_1",
        published_at=None,
        has_content=False,
    )
    client, _ = _client(
        tmp_path, articles=SourceArticlePage(total=1, offset=0, limit=30, items=[item])
    )

    with client:
        payload = client.get("/api/source/articles", headers=AUTH).json()

    assert payload["items"][0]["processed"] is True
    assert payload["items"][0]["published_at"] is None
