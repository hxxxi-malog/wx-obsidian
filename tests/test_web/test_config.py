"""配置接口测试：密钥掩码、掩码值不回写、白名单、目录浏览。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.web.api import MASK, create_app

TOKEN = "test-token-123"
AUTH = {"X-WX-Token": TOKEN}


@pytest.fixture
def env(tmp_path: Path) -> dict[str, Any]:
    cm = ConfigManager(config_dir=tmp_path / "cfg")
    cm.load()
    cm.set("llm.providers.deepseek.api_key", "sk-abcdefghijklmnop")
    cm.set("source.token", "bearer-secret-token")
    cm.save()

    runtime, _ = make_runtime(orchestrator=FakeOrchestrator(steps=[]), config_manager=cm)
    app = create_app(runtime, token=TOKEN, extra_allowed_hosts=frozenset({"testclient"}))
    return {"client": TestClient(app), "cm": cm, "cfg_dir": tmp_path / "cfg"}


def test_get_config_masks_secrets(env: dict[str, Any]) -> None:
    with env["client"] as client:
        payload = client.get("/api/config", headers=AUTH).json()

    provider_key = payload["llm"]["providers"]["deepseek"]["api_key"]
    assert provider_key.startswith("sk-a")
    assert MASK in provider_key
    assert "abcdefghijklmnop" not in provider_key
    assert "bearer-secret-token" not in payload["source"]["token"]
    assert payload["obsidian"]["articles_dir"]


def test_put_config_skips_masked_and_empty_values(env: dict[str, Any]) -> None:
    masked = f"sk-a{MASK}mnop"

    with env["client"] as client:
        response = client.put(
            "/api/config",
            json={"patch": {"llm.providers.deepseek.api_key": masked, "source.token": ""}},
            headers=AUTH,
        )

    body = response.json()
    assert body["saved"] is False
    assert set(body["skipped"]) == {"llm.providers.deepseek.api_key", "source.token"}

    # 原值必须完好
    reloaded = ConfigManager(config_dir=env["cfg_dir"])
    reloaded.load()
    assert reloaded.get("llm.providers.deepseek.api_key") == "sk-abcdefghijklmnop"
    assert reloaded.get("source.token") == "bearer-secret-token"


def test_put_config_applies_valid_changes(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.put(
            "/api/config",
            json={
                "patch": {
                    "fetch.max_days": "14",
                    "vision.enabled": "false",
                    "llm.providers.deepseek.model": "deepseek-v4-pro",
                }
            },
            headers=AUTH,
        )

    assert response.json()["saved"] is True

    cm: ConfigManager = env["cm"]
    assert cm.get("fetch.max_days") == 14
    assert cm.get("vision.enabled") is False
    assert cm.get("llm.providers.deepseek.model") == "deepseek-v4-pro"

    with env["client"] as client:
        assert client.get("/api/config", headers=AUTH).json()["fetch"]["max_days"] == 14


def test_put_config_rejects_unknown_key(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.put("/api/config", json={"patch": {"evil.key": "x"}}, headers=AUTH)
    assert response.status_code == 400
    assert "evil.key" in response.json()["detail"]


def test_put_config_rejects_bad_type(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.put(
            "/api/config", json={"patch": {"fetch.max_days": "abc"}}, headers=AUTH
        )
    assert response.status_code == 400
    assert "整数" in response.json()["detail"]


def test_put_config_rejects_unknown_channel(env: dict[str, Any]) -> None:
    with env["client"] as client:
        response = client.put("/api/config", json={"patch": {"llm.active": "nope"}}, headers=AUTH)
    assert response.status_code == 400
    assert "不存在" in response.json()["detail"]


def test_put_config_flags_restart_required_for_source(env: dict[str, Any]) -> None:
    # source 客户端在启动时构造，改完必须重启才生效
    with env["client"] as client:
        response = client.put(
            "/api/config",
            json={"patch": {"source.base_url": "http://127.0.0.1:9001"}},
            headers=AUTH,
        )
    assert response.json()["restart_required"] is True


def test_fs_list_returns_sorted_entries(tmp_path: Path, env: dict[str, Any]) -> None:
    root = tmp_path / "browse"
    (root / "sub").mkdir(parents=True)
    (root / "b.txt").write_text("x", encoding="utf-8")
    (root / "a.txt").write_text("x", encoding="utf-8")

    with env["client"] as client:
        payload = client.get(f"/api/fs/list?path={root}", headers=AUTH).json()

    assert payload["path"] == str(root)
    assert payload["parent"] == str(tmp_path)
    # 目录在前，然后按名称
    assert [entry["name"] for entry in payload["entries"]] == ["sub", "a.txt", "b.txt"]
    assert payload["entries"][0]["is_dir"] is True


def test_fs_list_error_cases(tmp_path: Path, env: dict[str, Any]) -> None:
    with env["client"] as client:
        assert client.get("/api/fs/list", headers=AUTH).status_code == 400
        assert client.get("/api/fs/list?path=relative/dir", headers=AUTH).status_code == 400
        assert client.get(f"/api/fs/list?path={tmp_path / 'nope'}", headers=AUTH).status_code == 404

        file_path = tmp_path / "a-file"
        file_path.write_text("x", encoding="utf-8")
        response = client.get(f"/api/fs/list?path={file_path}", headers=AUTH)
        assert response.status_code == 400
        assert response.json()["error"] == "not_a_directory"
