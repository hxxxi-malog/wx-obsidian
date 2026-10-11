"""文章接口测试：列表筛选分页、级联删除、在 Obsidian 中打开。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.output import cleanup
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
    # .obsidian 标记「这才是 Obsidian 认的仓库根」，与真实布局一致
    (articles_dir / ".obsidian").mkdir()
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
    # 删除路径会经 cascade_delete → cleanup.save_processed() 落盘。
    # conftest 的 autouse 隔离已兜底，这里再显式拦一层，让意图写在测试里。
    monkeypatch.setattr(cleanup, "save_processed", lambda *a, **k: None)

    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]), config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return {
        "client": TestClient(app),
        "note": note,
        "records": records,
        "vault": vault,
        "tmp": tmp_path,
    }


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

    def fake_run(cmd: list[str], **kwargs: Any) -> _OkResult:
        captured["cmd"] = cmd
        return _OkResult()

    # 注册表读不到时不拦人（本机没装 Obsidian 也要能用）
    monkeypatch.setattr(articles_api, "_OBSIDIAN_REGISTRY", env["tmp"] / "no-registry.json")
    monkeypatch.setattr(articles_api.subprocess, "run", fake_run)

    with env["client"] as client:
        response = client.post("/api/articles/a1/open", headers=AUTH)

    assert response.status_code == 200
    assert captured["cmd"][0] == "open"
    uri = captured["cmd"][1]
    assert uri.startswith("obsidian://open?path=")
    assert "注意力机制详解.md" in unquote(uri.split("path=", 1)[1])


# --- Obsidian 仓库注册表：真实踩过的坑（搬 vault 后 open 报 Vault not found）------------


def _write_registry(tmp_path: Path, vaults: list[Path]) -> Path:
    registry = tmp_path / "obsidian.json"
    registry.write_text(
        json.dumps({"vaults": {str(i): {"path": str(v)} for i, v in enumerate(vaults)}}),
        encoding="utf-8",
    )
    return registry


def test_open_falls_back_when_registry_missing(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读不到注册表（非 macOS / Obsidian 未装）时不能拦住用户。"""
    monkeypatch.setattr(articles_api, "_OBSIDIAN_REGISTRY", tmp_path / "nope.json")
    monkeypatch.setattr(articles_api.subprocess, "run", lambda *a, **k: _OkResult())

    with env["client"] as client:
        assert client.post("/api/articles/a1/open", headers=AUTH).status_code == 200


def test_open_rejects_note_outside_registered_vaults(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """笔记不在任何已注册仓库内 → 给可执行指引，而不是让 Obsidian 弹 Vault not found。"""
    other = tmp_path / "另一个仓库"
    other.mkdir()
    monkeypatch.setattr(articles_api, "_OBSIDIAN_REGISTRY", _write_registry(tmp_path, [other]))

    with env["client"] as client:
        response = client.post("/api/articles/a1/open", headers=AUTH)

    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "vault_not_registered"
    assert "打开文件夹作为仓库" in body["detail"]
    assert "另一个仓库" in body["detail"]  # 顺带列出已注册仓库，便于排查


def test_open_accepts_note_inside_registered_vault(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = env["vault"]
    monkeypatch.setattr(articles_api, "_OBSIDIAN_REGISTRY", _write_registry(tmp_path, [vault]))
    monkeypatch.setattr(articles_api.subprocess, "run", lambda *a, **k: _OkResult())

    with env["client"] as client:
        response = client.post("/api/articles/a1/open", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["vault"] == vault.name


def test_find_containing_vault_prefers_longest_match(tmp_path: Path) -> None:
    outer = tmp_path / "outer"
    inner = outer / "nested"
    note = inner / "dir" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("x", encoding="utf-8")

    assert articles_api.find_containing_vault(note, [outer, inner]) == inner.resolve()
    assert articles_api.find_containing_vault(note, [outer]) == outer.resolve()
    assert articles_api.find_containing_vault(note, [tmp_path / "unrelated"]) is None


def test_vault_message_points_at_dot_obsidian(tmp_path: Path) -> None:
    """提示要给出真正的仓库根（带 .obsidian 的那一层），而不是泛泛一句失败。"""
    vault_root = tmp_path / "公众号文章"
    note = vault_root / "Agent" / "笔记.md"
    note.parent.mkdir(parents=True)
    note.write_text("x", encoding="utf-8")
    (vault_root / ".obsidian").mkdir()

    assert articles_api.suggest_vault_root(note) == vault_root
    message = articles_api._vault_required_message(note, [vault_root])
    assert str(vault_root) in message
    assert "打开文件夹作为仓库" in message


def test_open_percent_encodes_special_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """标题里的 # 不编码会被当成 URI 片段，路径会被截断。"""
    note = tmp_path / "vault" / "公众号文章" / "话题 #1 & 更多?.md"
    note.parent.mkdir(parents=True)
    note.write_text("x", encoding="utf-8")

    captured: dict[str, str] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> _OkResult:
        captured["uri"] = cmd[1]
        return _OkResult()

    monkeypatch.setattr(articles_api.subprocess, "run", fake_run)
    articles_api.open_in_obsidian(str(note), registry_path=tmp_path / "no-registry.json")

    uri = captured["uri"]
    assert "%23" in uri  # #
    assert "%26" in uri  # &
    assert "%3F" in uri  # ?
    assert "#" not in uri
    assert uri.endswith(".md")
    assert unquote(uri.split("path=", 1)[1]) == str(note)


class _OkResult:
    """subprocess.run 的成功替身。"""

    returncode = 0
    stderr = ""
