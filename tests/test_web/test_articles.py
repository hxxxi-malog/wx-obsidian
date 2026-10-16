"""文章接口测试：列表筛选分页、级联删除、在 Obsidian 中打开。"""

from __future__ import annotations

import json
import sys
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

# 「在 Obsidian 中打开」走的是 macOS 的 `open obsidian://...`：非 macOS 时
# wx_obsidian/web/articles.py 会直接抛 RuntimeError("仅支持 macOS")，端点必然 400。
# 所以这组用例只在 macOS 上有意义——显式声明平台要求，而不是让它们在 Linux CI 上
# 长期红着（红久了就没人看这个 job 了）。代价是这组逻辑在 Linux 上不再被覆盖，
# 需要的话可以补一个 macOS runner 的 job，或把平台判定做成可注入的开关。
macos_only = pytest.mark.skipif(sys.platform != "darwin", reason="open_in_obsidian 仅支持 macOS")


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
        "cm": cm,
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


# --- N1 排序：白名单、缺字段排最后、同键跨页稳定 ---------------------------------------


def _sort_records() -> dict[str, Any]:
    """排序用例：a3/c1/e5 同键（processed_at 相同），d4 缺 processed_at，a3 缺 category。"""
    return {
        "b2": {"title": "Beta", "category": "LLM", "processed_at": "2026-05-02T10:00:00"},
        "a3": {"title": "Alpha", "category": "", "processed_at": "2026-05-01T10:00:00"},
        "c1": {"title": "Gamma", "category": "Agent", "processed_at": "2026-05-01T10:00:00"},
        "d4": {"title": "Alpha", "category": "LLM", "processed_at": ""},
        "e5": {"title": "Delta", "category": "Agent", "processed_at": "2026-05-01T10:00:00"},
    }


@pytest.fixture
def sort_env(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """只关心排序的隔离环境（不需要 vault）。"""
    records = _sort_records()
    monkeypatch.setattr(articles_api, "load_processed", lambda *a, **k: records)
    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]))
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app)


def _article_ids(client: TestClient, query: str = "") -> list[str]:
    payload = client.get(f"/api/articles{query}", headers=AUTH).json()
    return [item["article_id"] for item in payload["items"]]


def test_sort_default_order_is_unchanged(sort_env: TestClient) -> None:
    """不传 sort/order：与改动前逐条一致——processed_at 降序，同键保持 processed.json 顺序。"""
    with sort_env as client:
        assert _article_ids(client) == ["b2", "a3", "c1", "e5", "d4"]


@pytest.mark.parametrize(
    "query",
    [
        "?sort=unknown",
        "?order=sideways",
        "?sort=unknown&order=asc",
        "?sort=&order=",
        "?sort=Title&order=asc",  # 大小写敏感：Title 不在白名单
        "?sort=title&order=sideways",  # order 非法 → 整对回落
        "?sort=processed_at&order=",
    ],
)
def test_sort_invalid_values_fall_back_to_default(sort_env: TestClient, query: str) -> None:
    with sort_env as client:
        assert _article_ids(client, query) == _article_ids(client)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?sort=processed_at&order=desc", ["b2", "e5", "c1", "a3", "d4"]),
        ("?sort=processed_at&order=asc", ["a3", "c1", "e5", "b2", "d4"]),
        ("?sort=title&order=asc", ["a3", "d4", "b2", "e5", "c1"]),
        ("?sort=title&order=desc", ["c1", "e5", "b2", "d4", "a3"]),
        ("?sort=category&order=asc", ["c1", "e5", "b2", "d4", "a3"]),
        ("?sort=category&order=desc", ["d4", "b2", "e5", "c1", "a3"]),
    ],
)
def test_sort_whitelist_orders(sort_env: TestClient, query: str, expected: list[str]) -> None:
    """升/降序都对；同键用 article_id 做次级键；缺字段的项两个方向都排最后。"""
    with sort_env as client:
        assert _article_ids(client, query) == expected


@pytest.mark.parametrize(
    "query",
    [
        "",  # 默认顺序
        "?sort=processed_at&order=desc",
        "?sort=processed_at&order=asc",
        "?sort=title&order=asc",
        "?sort=category&order=desc",
    ],
)
def test_sort_is_stable_across_pages(sort_env: TestClient, query: str) -> None:
    """同键数据跨页切分不重不漏：排序必须发生在分页切片之前。"""
    separator = "&" if query else "?"
    with sort_env as client:
        full = _article_ids(client, query)
        paged: list[str] = []
        for offset in range(0, len(full), 2):
            page = client.get(
                f"/api/articles{query}{separator}limit=2&offset={offset}", headers=AUTH
            ).json()
            assert page["total"] == len(full)  # total 与分页无关
            paged.extend(item["article_id"] for item in page["items"])

    assert paged == full
    assert len(set(paged)) == len(full)  # 无重复


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


# --- N2 批量删除：部分失败、上限、vault 未配置、任务运行中、去重 ------------------------


def _bulk_client(env: dict[str, Any], orchestrator: FakeOrchestrator) -> TestClient:
    """用 env 的 vault/记录配置另建一个 app（用于换 orchestrator）。"""
    runtime, _ = make_runtime(orchestrator=orchestrator, config_manager=env["cm"])
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return TestClient(app)


def test_bulk_delete_all_success(env: dict[str, Any]) -> None:
    second = env["vault"] / "公众号文章" / "LLM" / "第二篇.md"
    second.write_text("# 第二篇\n", encoding="utf-8")
    env["records"]["a4"] = {
        "title": "第二篇",
        "status": "done",
        "category": "LLM",
        "file": str(second),
        "processed_at": "2026-10-04T10:00:00",
    }

    with env["client"] as client:
        response = client.post(
            "/api/articles/bulk-delete", json={"article_ids": ["a1", "a4"]}, headers=AUTH
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["deleted"] == 2
    assert body["failed"] == []
    assert body["actions"]  # 至少有「删除文件」/「从 processed.json 移除」
    assert not env["note"].exists()
    assert not second.exists()


def test_bulk_delete_reports_missing_ids_without_failing_batch(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.post(
            "/api/articles/bulk-delete",
            json={"article_ids": ["nope-1", "a1", "nope-2"]},
            headers=AUTH,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] == 1  # 未知 id 不整体失败
    assert body["failed"] == [
        {"article_id": "nope-1", "error": "article_not_found"},
        {"article_id": "nope-2", "error": "article_not_found"},
    ]
    assert not env["note"].exists()  # a1 依然被删掉


def test_bulk_delete_dedups_ids(env: dict[str, Any]) -> None:
    """重复 id 只删一次：不去重的话第二次会变成 article_not_found。"""
    with env["client"] as client:
        body = client.post(
            "/api/articles/bulk-delete",
            json={"article_ids": ["a1", "a1", "a1"]},
            headers=AUTH,
        ).json()

    assert body["deleted"] == 1
    assert body["failed"] == []


def test_bulk_delete_dedups_actions(env: dict[str, Any]) -> None:
    """每条成功项都会产出「已从 processed.json 移除记录」；摘要里只留一条。"""
    with env["client"] as client:
        body = client.post(
            "/api/articles/bulk-delete", json={"article_ids": ["a1", "a3"]}, headers=AUTH
        ).json()

    assert body["deleted"] == 2
    assert body["actions"].count("已从 processed.json 移除记录") == 1
    assert len(body["actions"]) == len(set(body["actions"]))


def test_bulk_delete_rejects_empty_oversized_and_malformed(env: dict[str, Any]) -> None:
    with env["client"] as client:
        empty = client.post("/api/articles/bulk-delete", json={"article_ids": []}, headers=AUTH)
        oversized = client.post(
            "/api/articles/bulk-delete",
            json={"article_ids": [f"a{index}" for index in range(501)]},
            headers=AUTH,
        )
        boundary = client.post(
            "/api/articles/bulk-delete",
            json={"article_ids": [f"x{index}" for index in range(500)]},
            headers=AUTH,
        )
        malformed = client.post(
            "/api/articles/bulk-delete", json={"article_ids": "a1"}, headers=AUTH
        )

    assert empty.status_code == 400
    assert empty.json()["error"] == "bad_request"
    assert "不能为空" in empty.json()["detail"]

    assert oversized.status_code == 400
    assert oversized.json()["error"] == "bad_request"
    assert "500" in oversized.json()["detail"]

    assert boundary.status_code == 200  # 边界值 500 允许
    assert len(boundary.json()["failed"]) == 500

    assert malformed.status_code == 400
    assert malformed.json()["error"] == "bad_request"


def test_bulk_delete_requires_vault_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """vault 未配置 → 与单条 DELETE 同样的 400 not_configured。"""
    records = _records(tmp_path / "not-used.md")
    monkeypatch.setattr(articles_api, "load_processed", lambda *a, **k: records)

    cm = ConfigManager(config_dir=tmp_path / "cfg-no-vault")
    cm.load()
    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]), config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    with TestClient(app) as client:
        response = client.post(
            "/api/articles/bulk-delete", json={"article_ids": ["a1"]}, headers=AUTH
        )

    assert response.status_code == 400
    assert response.json()["error"] == "not_configured"


def test_bulk_delete_is_rejected_while_job_runs(env: dict[str, Any]) -> None:
    """有任务在跑时拒绝：避免与管线争抢 processed.json / MOC 写入。"""
    slow = FakeOrchestrator(steps=["文章"] * 20, step_delay=0.05)
    with _bulk_client(env, slow) as client:
        job_id = client.post("/api/jobs", json={"kind": "fetch"}, headers=AUTH).json()["job_id"]
        response = client.post(
            "/api/articles/bulk-delete", json={"article_ids": ["a1"]}, headers=AUTH
        )
        # 等任务跑完，避免上下文退出时留下悬挂任务
        client.get(f"/api/jobs/{job_id}/events", headers=AUTH)

    assert response.status_code == 409
    assert response.json()["error"] == "job_busy"
    assert "processed.json" in response.json()["detail"]
    assert env["note"].exists()  # 被拒绝时一条都不删


def test_open_missing_file_is_400(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.post("/api/articles/a2/open", headers=AUTH)
    assert response.status_code == 400
    assert response.json()["error"] == "open_failed"


@macos_only
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


@macos_only
def test_open_falls_back_when_registry_missing(
    env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """读不到注册表（Obsidian 未装 / 注册表损坏）时不能拦住用户。"""
    monkeypatch.setattr(articles_api, "_OBSIDIAN_REGISTRY", tmp_path / "nope.json")
    monkeypatch.setattr(articles_api.subprocess, "run", lambda *a, **k: _OkResult())

    with env["client"] as client:
        assert client.post("/api/articles/a1/open", headers=AUTH).status_code == 200


@macos_only
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


@macos_only
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


@macos_only
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
