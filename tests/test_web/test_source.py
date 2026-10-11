"""数据源 / 公众号 / 账号接口测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import Feed
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _client(tmp_path: Path, orchestrator: FakeOrchestrator) -> TestClient:
    cm = ConfigManager(config_dir=tmp_path / "cfg")
    cm.load()
    cm.set("source.type", "we_mp_rss")
    cm.set("source.base_url", "http://127.0.0.1:8001")
    runtime, _ = make_runtime(orchestrator=orchestrator, config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app)


def test_source_health_reports_config_and_docker_hint(tmp_path: Path) -> None:
    with _client(tmp_path, FakeOrchestrator(source_healthy=True)) as client:
        payload = client.get("/api/source/health", headers=AUTH).json()
    assert payload["healthy"] is True
    assert payload["base_url"] == "http://127.0.0.1:8001"
    assert "docker compose" in payload["docker_hint"]


def test_source_health_survives_probe_failure(tmp_path: Path) -> None:
    """探活抛异常时应报「不健康」，而不是 500。"""

    class _Broken(FakeOrchestrator):
        def is_source_healthy(self) -> bool:
            raise ConnectionError("连不上")

    with _client(tmp_path, _Broken()) as client:
        response = client.get("/api/source/health", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["healthy"] is False


def test_account_status_and_refresh(tmp_path: Path) -> None:
    fake = FakeOrchestrator(account_logged_in=True)
    with _client(tmp_path, fake) as client:
        account = client.get("/api/source/account", headers=AUTH).json()
        assert account["is_logged_in"] is True
        assert account["username"] == "测试账号"

        assert client.get("/api/source/login-url", headers=AUTH).json()["url"].endswith("/login")
        assert client.post("/api/source/refresh", headers=AUTH).json()["ok"] is True

    assert fake.refreshed == 1


def test_feeds_list_add_delete(tmp_path: Path) -> None:
    fake = FakeOrchestrator(feeds=[Feed(id="f1", name="阮一峰的网络日志")])
    with _client(tmp_path, fake) as client:
        feeds = client.get("/api/feeds", headers=AUTH).json()
        assert [feed["name"] for feed in feeds] == ["阮一峰的网络日志"]

        added = client.post(
            "/api/feeds", json={"article_url": "https://mp.weixin.qq.com/s/abc"}, headers=AUTH
        )
        assert added.status_code == 200
        assert added.json()["id"] == "feed-new"

        assert client.delete("/api/feeds/f1", headers=AUTH).json()["ok"] is True
        assert client.delete("/api/feeds/f1", headers=AUTH).status_code == 404


def test_add_feed_requires_url(tmp_path: Path) -> None:
    with _client(tmp_path, FakeOrchestrator()) as client:
        response = client.post("/api/feeds", json={}, headers=AUTH)
    assert response.status_code == 400


def test_add_feed_unsupported_returns_501(tmp_path: Path) -> None:
    """数据源不支持按链接订阅时要明确告知，而不是假装成功。"""
    with _client(tmp_path, FakeOrchestrator(add_feed_supported=False)) as client:
        response = client.post(
            "/api/feeds", json={"article_url": "https://mp.weixin.qq.com/s/abc"}, headers=AUTH
        )
    assert response.status_code == 501
    assert "we-mp-rss" in response.json()["detail"]


@pytest.mark.parametrize("value", ["", "  "])
def test_add_feed_blank_url_is_rejected(tmp_path: Path, value: str) -> None:
    with _client(tmp_path, FakeOrchestrator()) as client:
        assert (
            client.post("/api/feeds", json={"article_url": value}, headers=AUTH).status_code == 400
        )
