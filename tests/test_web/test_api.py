"""P0 接口测试：/api/ping、/api/health、/api/stats 与 SPA 静态回退。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_web.helpers import make_fake_runtime
from wx_obsidian.web import api as web_api

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _client(healthy: bool = True) -> TestClient:
    app = web_api.create_app(
        make_fake_runtime(healthy=healthy),
        token=TOKEN,
        extra_allowed_hosts=frozenset({"testclient"}),
    )
    return TestClient(app)


def _make_static(tmp_path: Path, content: str = "INDEX") -> Path:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text(content, encoding="utf-8")
    return static


def test_health_shape_and_overall_true() -> None:
    with _client() as client:
        payload = client.get("/api/health", headers=AUTH).json()
    assert {"source", "llm_api", "vision_api", "vault_path", "overall"} <= set(payload)
    assert payload["overall"] is True
    assert payload["source"]["success"] is True
    assert payload["source"]["latency_ms"] == 12.5


def test_health_overall_false_when_degraded() -> None:
    with _client(healthy=False) as client:
        payload = client.get("/api/health", headers=AUTH).json()
    assert payload["overall"] is False
    assert payload["source"]["message"] == "连接失败"


def test_stats_passthrough() -> None:
    with _client() as client:
        payload = client.get("/api/stats", headers=AUTH).json()
    assert payload["processed_articles"] == 4
    assert payload["failed_articles"] == 1
    assert payload["categories"] == {"LLM": 3, "Agent架构": 1}


def test_unknown_api_route_returns_404_json() -> None:
    with _client() as client:
        response = client.get("/api/nope", headers=AUTH)
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


def test_spa_fallback_reports_not_built(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_api, "STATIC_DIR", tmp_path / "static")
    with _client() as client:
        response = client.get("/")
    assert response.status_code == 503
    assert response.json()["error"] == "frontend_not_built"


def test_static_is_not_token_protected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_api, "STATIC_DIR", _make_static(tmp_path))
    with _client() as client:
        response = client.get("/")  # 不带 token：静态资源公开，接口才要鉴权
    assert response.status_code == 200
    assert "INDEX" in response.text


def test_spa_fallback_serves_index_for_deep_route(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_api, "STATIC_DIR", _make_static(tmp_path))
    with _client() as client:
        response = client.get("/articles")
    assert response.status_code == 200
    assert "INDEX" in response.text


def test_spa_fallback_blocks_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(web_api, "STATIC_DIR", _make_static(tmp_path))
    (tmp_path / "secret.txt").write_text("SECRET", encoding="utf-8")
    with _client() as client:
        response = client.get("/..%2Fsecret.txt")
    assert "SECRET" not in response.text


def test_health_reports_first_run_and_vault_warning(tmp_path) -> None:
    """空配置目录 = 首启，且直接给出「还没设 Vault」的可操作提示。"""
    with _client() as client:
        payload = client.get("/api/health", headers=AUTH).json()

    assert payload["first_run"] is True
    assert [item["code"] for item in payload["warnings"]] == ["vault_unset"]


def test_health_warns_about_tcc_sensitive_vault(tmp_path) -> None:
    """Vault 落在 ~/Downloads 时必须提示迁移（原 LaunchAgent 就栽在这）。"""
    downloads = str(Path.home() / "Downloads")
    with _client() as client:
        saved = client.put(
            "/api/config",
            json={"patch": {"obsidian.vault_path": downloads}},
            headers=AUTH,
        )
        assert saved.status_code == 200
        payload = client.get("/api/health", headers=AUTH).json()

    codes = [item["code"] for item in payload["warnings"]]
    assert "vault_in_tcc_dir" in codes
    assert all(item["action"] for item in payload["warnings"])


def test_health_warns_when_vault_missing(tmp_path) -> None:
    with _client() as client:
        client.put(
            "/api/config",
            json={"patch": {"obsidian.vault_path": str(tmp_path / "nope")}},
            headers=AUTH,
        )
        payload = client.get("/api/health", headers=AUTH).json()

    assert "vault_missing" in [item["code"] for item in payload["warnings"]]
