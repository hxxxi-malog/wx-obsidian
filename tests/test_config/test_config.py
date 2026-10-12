"""config.py 测试：常量、processed.json 持久化、Skill 加载、Vault 扫描。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import wx_obsidian.config as config_module
from wx_obsidian.config import (
    MAX_ARTICLE_LENGTH,
    MAX_PROMPT_CONTENT,
    SUB_TOPIC_THRESHOLD,
    env_flag,
    load_processed,
    load_skill,
    save_processed,
)


@pytest.fixture
def processed_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 PROCESSED_FILE 指向临时目录，避免读写用户真实状态文件。"""
    target = tmp_path / "state" / "processed.json"
    monkeypatch.setattr(config_module, "PROCESSED_FILE", target)
    return target


@pytest.fixture
def skills_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 SKILLS_DIR 指向临时目录，并清空 load_skill 的缓存。"""
    target = tmp_path / "skills"
    target.mkdir()
    monkeypatch.setattr(config_module, "SKILLS_DIR", target)
    load_skill.cache_clear()
    yield target
    load_skill.cache_clear()


class TestConstants:
    """常量值验证（以 config.py 当前取值为准）。"""

    def test_max_article_length(self):
        assert MAX_ARTICLE_LENGTH == 100000

    def test_max_prompt_content(self):
        assert MAX_PROMPT_CONTENT == 100000

    def test_sub_topic_threshold(self):
        assert SUB_TOPIC_THRESHOLD == 3


class TestLoadProcessed:
    """load_processed() 测试。"""

    def test_normal_load(self, processed_path: Path):
        data = {"id1": {"title": "文章1", "status": "done"}}
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        assert load_processed() == data

    def test_missing_file_returns_empty(self, processed_path: Path):
        """文件不存在时返回空字典，且不创建文件。"""
        assert not processed_path.exists()
        assert load_processed() == {}
        assert not processed_path.exists()

    def test_corrupted_json_returns_empty(self, processed_path: Path):
        """JSON 损坏时返回空字典并打印警告。"""
        processed_path.parent.mkdir(parents=True, exist_ok=True)
        processed_path.write_text('{"id1": {"title": "broken"', encoding="utf-8")

        with patch("builtins.print") as mock_print:
            assert load_processed() == {}

        assert mock_print.called

    def test_unreadable_path_returns_empty(self, processed_path: Path):
        """目标是目录等 OSError 场景下返回空字典而不是抛异常。"""
        processed_path.mkdir(parents=True, exist_ok=True)
        assert load_processed() == {}


class TestSaveProcessed:
    """save_processed() 原子写入测试。"""

    def test_atomic_write_creates_file(self, processed_path: Path):
        data = {"id1": {"title": "文章1", "status": "done"}}
        save_processed(data)

        assert json.loads(processed_path.read_text(encoding="utf-8")) == data

    def test_atomic_write_leaves_no_temp_file(self, processed_path: Path):
        save_processed({"test": "value"})

        assert list(processed_path.parent.glob(".*.tmp")) == []
        assert processed_path.exists()

    def test_atomic_write_preserves_old_on_failure(self, processed_path: Path):
        """os.replace 失败时原文件不被破坏，临时文件被清理。"""
        old_data = {"id1": {"title": "旧数据"}}
        save_processed(old_data)

        with (
            patch("os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            save_processed({"id1": {"title": "新数据"}})

        assert json.loads(processed_path.read_text(encoding="utf-8")) == old_data
        assert list(processed_path.parent.glob(".*.tmp")) == []

    def test_unicode_content(self, processed_path: Path):
        """正确保存中文和 emoji 内容。"""
        data = {"id1": {"title": "🎉 测试文章：AI 时代", "tags": ["人工智能", "深度学习"]}}
        save_processed(data)

        result = json.loads(processed_path.read_text(encoding="utf-8"))
        assert result["id1"]["title"] == "🎉 测试文章：AI 时代"
        assert result["id1"]["tags"] == ["人工智能", "深度学习"]


class TestLoadSkill:
    """load_skill() 测试。"""

    @staticmethod
    def _write_skill(skills_dir: Path, name: str, text: str) -> None:
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

    def test_strip_frontmatter(self, skills_dir: Path):
        """正确剥离 YAML frontmatter。"""
        self._write_skill(
            skills_dir,
            "demo",
            "---\nname: test\ndescription: desc\n---\n\n# 标题\n\n内容",
        )

        assert load_skill("demo") == "# 标题\n\n内容"

    def test_no_frontmatter_returns_empty(self, skills_dir: Path):
        """没有成对 frontmatter 分隔符时按当前契约返回空字符串。"""
        self._write_skill(skills_dir, "demo", "# 只有标题\n\n没有 frontmatter")

        assert load_skill("demo") == ""

    def test_empty_skill_file(self, skills_dir: Path):
        self._write_skill(skills_dir, "demo", "")

        assert load_skill("demo") == ""

    def test_missing_skill_returns_empty(self, skills_dir: Path):
        assert load_skill("not-exist") == ""


class TestScanExistingContent:
    """scan_existing_content 测试（实现在 vault.py）。"""

    def test_scan_normal(self, tmp_vault):
        """正常扫描已有文章和概念。"""
        vault_path, articles_dir = tmp_vault

        # 创建分类目录和文章
        llm_dir = articles_dir / "LLM"
        llm_dir.mkdir()
        (llm_dir / "文章1.md").write_text("# 文章1")
        (llm_dir / "文章2.md").write_text("# 文章2")
        (llm_dir / "_MOC.md").write_text("# LLM MOC")

        # 创建概念目录
        concept_dir = articles_dir / "概念"
        concept_dir.mkdir()
        (concept_dir / "概念A.md").write_text("# 概念A")

        from wx_obsidian.output.vault import scan_existing_content

        articles, concepts = scan_existing_content(vault_path, "公众号文章")

        assert "文章1" in articles
        assert "文章2" in articles
        assert "概念A" in concepts
        assert "_MOC" not in articles

    def test_scan_empty_vault(self, tmp_vault):
        """空 Vault 返回空列表。"""
        vault_path, _ = tmp_vault
        from wx_obsidian.output.vault import scan_existing_content

        articles, concepts = scan_existing_content(vault_path, "公众号文章")
        assert articles == []
        assert concepts == []

    def test_scan_nonexistent_dir(self, tmp_path: Path):
        """不存在的目录返回空列表。"""
        from wx_obsidian.output.vault import scan_existing_content

        articles, concepts = scan_existing_content(tmp_path / "nonexistent", "公众号文章")
        assert articles == []
        assert concepts == []


# ---------------------------------------------------------------------------
# 账本写入护栏（来自一次真事故）
# ---------------------------------------------------------------------------


def test_save_processed_refuses_catastrophic_shrink(tmp_path: Path) -> None:
    """账本从大量条目塌缩到一小撮时必须拒绝写入。

    真实事故：某个测试走了 cascade_delete → save_processed()（默认路径），
    把 3 条 fixture 记录写进了用户 373 条的账本，去重状态被抹掉。
    """
    target = tmp_path / "processed.json"
    big = {f"id{i}": {"status": "done"} for i in range(100)}
    big["last_fetch_date"] = "2026-10-01"
    save_processed(big, target)

    with pytest.raises(ValueError, match="拒绝写入"):
        save_processed({"a2": {"status": "done"}, "a3": {"status": "done"}}, target)

    # 原文件必须原样保留
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 101

    # 显式 force 才允许重建（恢复脚本用）
    save_processed({"a2": {"status": "done"}}, target, force=True)
    assert json.loads(target.read_text(encoding="utf-8")) == {"a2": {"status": "done"}}


def test_save_processed_allows_normal_growth_and_shrink(tmp_path: Path) -> None:
    """正常增减（管线追加、级联删除删 1 条）不能被护栏误伤。"""
    target = tmp_path / "processed.json"
    records = {f"id{i}": {"status": "done"} for i in range(100)}
    save_processed(records, target)

    records["new"] = {"status": "done"}
    save_processed(records, target)
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 101

    del records[next(iter(records))]
    save_processed(records, target)  # 删 1 条：正常
    assert len(json.loads(target.read_text(encoding="utf-8"))) == 100


def test_conftest_isolates_real_processed_path(tmp_path: Path) -> None:
    """autouse 隔离必须生效：否则任何测试都可能写坏用户真实账本。"""
    import wx_obsidian.config as config_module
    import wx_obsidian.processing.similarity as similarity_module

    real = Path.home() / ".wx-obsidian"
    assert real not in config_module.PROCESSED_FILE.parents
    assert real not in similarity_module._DEFAULT_DB_PATH.parents
    save_processed({"only": {"status": "done"}})  # 默认路径也必须落在临时目录
    assert config_module.PROCESSED_FILE.exists()


# ---------------------------------------------------------------------------
# env_flag：按取值解析，不能按「是否存在」判断
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        (" On ", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
        (" false ", False),
    ],
)
def test_env_flag_parses_value(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("WX_TEST_FLAG", raw)
    assert env_flag("WX_TEST_FLAG") is expected


def test_env_flag_missing_uses_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WX_TEST_FLAG", raising=False)
    assert env_flag("WX_TEST_FLAG") is False
    assert env_flag("WX_TEST_FLAG", default=True) is True


def test_env_flag_unknown_value_falls_back_and_warns(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """拼错的取值必须 fail-closed，且留下告警，不能静默当开启。"""
    monkeypatch.setenv("WX_TEST_FLAG", "maybe")
    with caplog.at_level(logging.WARNING):
        assert env_flag("WX_TEST_FLAG") is False
        assert env_flag("WX_TEST_FLAG", default=True) is True
    assert "不是合法布尔值" in caplog.text


def test_debug_zero_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """回归：.env 模板写的就是 DEBUG=0，存在性判断会把它当成「开启」。"""
    monkeypatch.setenv("DEBUG", "0")
    assert env_flag("DEBUG") is False
