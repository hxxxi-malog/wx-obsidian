"""Vault 迁移测试：配置改写 + processed.json 绝对路径重写。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.vault_migration import migrate_vault_path

ARTICLES = "公众号文章"


def _setup(
    tmp_path: Path,
    *,
    old_vault: Path,
    records: dict,
    make_new_dir: bool = True,
) -> tuple[ConfigManager, Path, Path, Path]:
    """搭一个隔离环境：独立 config_dir + 独立 processed.json。

    Returns:
        (config_manager, new_vault, processed_file, config_dir)
    """
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    cm = ConfigManager(config_dir=config_dir)
    cm.load()
    cm.set("obsidian.vault_path", str(old_vault))
    cm.set("obsidian.articles_dir", ARTICLES)
    cm.save()

    new_vault = tmp_path / "new"
    if make_new_dir:
        (new_vault / ARTICLES).mkdir(parents=True, exist_ok=True)

    processed_file = config_dir / "processed.json"
    processed_file.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return cm, new_vault, processed_file, config_dir


def test_rewrites_paths_and_updates_config(tmp_path: Path) -> None:
    old_vault = tmp_path / "old"
    note = old_vault / ARTICLES / "LLM" / "a.md"
    new_note = tmp_path / "new" / ARTICLES / "LLM" / "a.md"
    new_note.parent.mkdir(parents=True, exist_ok=True)
    new_note.write_text("x", encoding="utf-8")

    cm, new_vault, processed_file, config_dir = _setup(
        tmp_path,
        old_vault=old_vault,
        records={"a1": {"title": "A", "status": "done", "file": str(note)}},
    )

    report = migrate_vault_path(new_vault, config_manager=cm, processed_file=processed_file)

    assert report.rewritten == 1
    assert report.missing_after == 0
    assert report.samples == ["LLM/a.md"]

    data = json.loads(processed_file.read_text(encoding="utf-8"))
    assert data["a1"]["file"] == str(new_note)

    reloaded = ConfigManager(config_dir=config_dir)
    reloaded.load()
    assert reloaded.get("obsidian.vault_path") == str(new_vault)


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    old_vault = tmp_path / "old"
    note = old_vault / ARTICLES / "b.md"
    cm, new_vault, processed_file, _ = _setup(
        tmp_path,
        old_vault=old_vault,
        records={"b1": {"status": "done", "file": str(note)}},
    )

    report = migrate_vault_path(
        new_vault, config_manager=cm, processed_file=processed_file, dry_run=True
    )

    assert report.rewritten == 1
    assert report.dry_run is True
    data = json.loads(processed_file.read_text(encoding="utf-8"))
    assert data["b1"]["file"] == str(note)
    assert cm.get("obsidian.vault_path") == str(old_vault)


def test_leaves_unrelated_and_legacy_records_alone(tmp_path: Path) -> None:
    old_vault = tmp_path / "old"
    outside = Path("/somewhere/else/note.md")
    cm, new_vault, processed_file, _ = _setup(
        tmp_path,
        old_vault=old_vault,
        records={
            "ok": {"status": "done", "file": str(old_vault / ARTICLES / "c.md")},
            "outside": {"status": "done", "file": str(outside)},
            "legacy": "some-string-record",
            "nofile": {"status": "skipped", "reason": "no_content"},
        },
    )

    report = migrate_vault_path(new_vault, config_manager=cm, processed_file=processed_file)

    assert report.rewritten == 1
    assert report.outside == 1
    data = json.loads(processed_file.read_text(encoding="utf-8"))
    assert data["outside"]["file"] == str(outside)
    assert data["legacy"] == "some-string-record"
    assert "file" not in data["nofile"]


def test_counts_records_whose_file_is_still_missing(tmp_path: Path) -> None:
    old_vault = tmp_path / "old"
    cm, new_vault, processed_file, _ = _setup(
        tmp_path,
        old_vault=old_vault,
        records={"gone": {"status": "done", "file": str(old_vault / ARTICLES / "gone.md")}},
    )

    report = migrate_vault_path(new_vault, config_manager=cm, processed_file=processed_file)

    assert report.rewritten == 1
    assert report.missing_after == 1  # 重写后目标文件仍不存在，需要人工确认


def test_rejects_missing_articles_dir(tmp_path: Path) -> None:
    old_vault = tmp_path / "old"
    cm, new_vault, processed_file, _ = _setup(
        tmp_path, old_vault=old_vault, records={}, make_new_dir=False
    )

    with pytest.raises(FileNotFoundError, match="找不到文章目录"):
        migrate_vault_path(new_vault, config_manager=cm, processed_file=processed_file)


def test_rejects_unconfigured_old_vault(tmp_path: Path) -> None:
    config_dir = tmp_path / "cfg"
    config_dir.mkdir(parents=True, exist_ok=True)
    cm = ConfigManager(config_dir=config_dir)
    cm.load()
    new_vault = tmp_path / "new"
    (new_vault / ARTICLES).mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="obsidian.vault_path"):
        migrate_vault_path(new_vault, config_manager=cm, processed_file=tmp_path / "p.json")
