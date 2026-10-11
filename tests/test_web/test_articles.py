"""文章接口测试：列表筛选分页、级联删除、在 Obsidian 中打开。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.web import articles as articles_api
from wx_obsidian.web.api import create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


def _records(note_path: Path) -> dict[str, Any]:
    return {
        "last_fetch_date": "2026-10-01",  # 非文章记录，必须被跳过
        "a1": {
            "title": "注意力机制详解",
            "status": "done",
            "category": "LLM",
            "sub_topic": "注意力机制",
            "file": str(note_path),
            "processed_at": "2026-10-02T10:00:00",
            "concepts": ["Self-Attention"],
            "tags": ["Transformer"],
        },
        "a2": {
            "title": "Agent 架构综述",
            "status": "error",
            "category": "Agent架构",
            "file": "",
            "processed_at": "2026-10-03T10:00:00",
            "error": "LLM 超时",
            "retry_count": 2,
        },
        "a3": {
            "title": "旧的笔记",
            "status": "done",
            "category": "LLM",
            "file": "",
            "processed_at": "",
        },
    }


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """隔离环境：临时 config + 临时 vault + 假的 processed.json。"""
    vault = tmp_path / "vault"
    articles_dir = vault / "公众号文章"
    category_dir = articles_dir / "LLM"
    category_dir.mkdir(parents=True)
    note = category_dir / "注意力机制详解.md"
    note.write_text("# 注意力机制详解\n", encoding="utf-8")
    (category_dir / "_MOC.md").write_text("# LLM\n\n- [[注意力机制详解]]\n", encoding="utf-8")

    cm = ConfigManager(config_dir=tmp_path / "cfg")
    cm.load()
    cm.set("obsidian.vault_path", str(vault))
    cm.set("obsidian.articles_dir", "公众号文章")
    cm.save()

    records = _records(note)
    monkeypatch.setattr(articles_api, "load_processed", lambda *a, **k: records)

    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]), config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return {"client": TestClient(app), "note": note, "records": records, "vault": vault}


def test_list_skips_non_article_records(env: dict[str, Any]) -> None:
    with env["client"] as client:
        payload = client.get("/api/articles", headers=AUTH).json()
    assert payload["total"] == 3  # last_fetch_date 不算文章
    assert [item["article_id"] for item in payload["items"]] == ["a2", "a1", "a3"]  # 新的在前
    assert payload["categories"] == ["Agent架构", "LLM"]


def test_list_search_and_filters(env: dict[str, Any]) -> None:
    with env["client"] as client:
        assert client.get("/api/articles?q=注意力", headers=AUTH).json()["total"] == 1
        assert client.get("/api/articles?category=LLM", headers=AUTH).json()["total"] == 2
        assert client.get("/api/articles?status=error", headers=AUTH).json()["total"] == 1

        paged = client.get("/api/articles?limit=1&offset=1", headers=AUTH).json()
        assert paged["total"] == 3
        assert len(paged["items"]) == 1
        assert paged["items"][0]["article_id"] == "a1"


def test_list_reports_file_existence(env: dict[str, Any]) -> None:
    with env["client"] as client:
        items = {
            item["article_id"]: item
            for item in client.get("/api/articles", headers=AUTH).json()["items"]
        }
    assert items["a1"]["file_exists"] is True
    assert items["a2"]["file_exists"] is False
    assert items["a2"]["error"] == "LLM 超时"


def test_detail_and_404(env: dict[str, Any]) -> None:
    with env["client"] as client:
        detail = client.get("/api/articles/a1", headers=AUTH).json()
        assert detail["title"] == "注意力机制详解"
        assert detail["concepts"] == ["Self-Attention"]
        assert client.get("/api/articles/nope", headers=AUTH).status_code == 404


def test_delete_cascades_and_returns_actions(env: dict[str, Any]) -> None:
    note: Path = env["note"]
    assert note.exists()
    with env["client"] as client:
        response = client.delete("/api/articles/a1", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["actions"]  # 至少有「删除笔记」
    assert not note.exists()


def test_delete_unknown_article_is_404(env: dict[str, Any]) -> None:
    with env["client"] as client:
        assert client.delete("/api/articles/nope", headers=AUTH).status_code == 404


def test_open_missing_file_is_400(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.post("/api/articles/a2/open", headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"] == "open_failed"


def test_open_builds_obsidian_uri(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Result:
        returncode = 0
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: Any) -> _Result:
        captured["cmd"] = cmd
        return _Result()

    monkeypatch.setattr(articles_api.subprocess, "run", fake_run)

    with env["client"] as client:
        response = client.post("/api/articles/a1/open", headers=AUTH)

    assert response.status_code == 200
    assert captured["cmd"][0] == "open"
    assert captured["cmd"][1].startswith("obsidian://open?path=")
    assert "注意力机制详解.md" in captured["cmd"][1]
