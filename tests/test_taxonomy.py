"""taxonomy.py 测试：分类体系声明的加载、主题词归一化与 prompt 词表渲染。"""

from __future__ import annotations

from pathlib import Path

import pytest

import wx_obsidian.taxonomy as taxonomy_module
from wx_obsidian.taxonomy import (
    VOCABULARY_CHAR_LIMIT,
    VOCABULARY_LIMIT,
    canonical_sub_topic,
    format_vocabulary,
    load_taxonomy,
    sub_topic_vocabulary,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清缓存，避免跨用例污染。"""
    load_taxonomy.cache_clear()
    yield
    load_taxonomy.cache_clear()


class TestCanonicalSubTopic:
    """别名归一化。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Loop工程", "Loop Engineering"),
            ("循环工程", "Loop Engineering"),
            ("Harness工程", "Harness Engineering"),
            ("Harness 工程", "Harness Engineering"),
            ("多智能体协同", "多Agent协同"),
            ("上下文管理", "上下文工程"),
            ("提示词工程", "Prompt工程"),
        ],
    )
    def test_maps_known_aliases(self, raw: str, expected: str):
        assert canonical_sub_topic(raw) == expected

    def test_ignores_case_and_spaces(self):
        assert canonical_sub_topic("  loop工程  ") == "Loop Engineering"
        assert canonical_sub_topic("LOOP工程") == "Loop Engineering"

    def test_keeps_canonical_and_unknown_values(self):
        """规范写法与未知主题都必须原样返回——归一化不能妨碍新增主题。"""
        assert canonical_sub_topic("Loop Engineering") == "Loop Engineering"
        assert canonical_sub_topic("某个全新主题") == "某个全新主题"
        assert canonical_sub_topic("vLLM 部署实践") == "vLLM 部署实践"

    def test_empty_and_whitespace(self):
        assert canonical_sub_topic("") == ""
        assert canonical_sub_topic("   ") == ""


class TestLoadTaxonomy:
    """声明文件的加载与降级。"""

    def test_loads_real_declaration(self):
        data = load_taxonomy()
        assert data["version"] >= 1
        assert data["aliases"]["Loop工程"] == "Loop Engineering"
        assert any(item["name"] == "多Agent协同" for item in data["sub_topics"])

    def test_missing_file_degrades_to_identity(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", tmp_path / "nope.yaml")
        load_taxonomy.cache_clear()
        assert load_taxonomy()["aliases"] == {}
        assert canonical_sub_topic("Loop工程") == "Loop工程"  # 恒等映射，不抛异常

    def test_broken_yaml_degrades_without_raising(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        broken = tmp_path / "taxonomy.yaml"
        broken.write_text("aliases: [这不是映射\n  - 也: 不是\n", encoding="utf-8")
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", broken)
        load_taxonomy.cache_clear()
        assert load_taxonomy()["aliases"] == {}
        assert canonical_sub_topic("Loop工程") == "Loop工程"

    def test_non_mapping_declaration_degrades(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        weird = tmp_path / "taxonomy.yaml"
        weird.write_text("- 只是\n- 一个列表\n", encoding="utf-8")
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", weird)
        load_taxonomy.cache_clear()
        assert load_taxonomy()["sub_topics"] == []

    def test_skips_malformed_entries(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        mixed = tmp_path / "taxonomy.yaml"
        mixed.write_text(
            "version: 2\naliases:\n  甲: 乙\n  空: ''\nsub_topics:\n"
            "  - name: 好主题\n    scope: 范围\n  - scope: 缺名字\n  - 不是映射\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", mixed)
        load_taxonomy.cache_clear()
        data = load_taxonomy()
        assert data["aliases"] == {"甲": "乙"}  # 空目标被丢弃
        assert [i["name"] for i in data["sub_topics"]] == ["好主题"]

    def test_cache_returns_same_object(self):
        assert load_taxonomy() is load_taxonomy()


class TestVocabulary:
    """受控词表与 prompt 渲染。"""

    def test_vocabulary_has_name_and_scope(self):
        items = sub_topic_vocabulary()
        assert items, "真实声明应当有词表"
        assert all(name and isinstance(scope, str) for name, scope in items)

    def test_respects_limits(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        big = tmp_path / "taxonomy.yaml"
        rows = "\n".join(f"  - name: 主题{i}\n    scope: {'x' * 50}" for i in range(100))
        big.write_text(f"version: 1\nsub_topics:\n{rows}\n", encoding="utf-8")
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", big)
        load_taxonomy.cache_clear()

        items = sub_topic_vocabulary()
        assert len(items) <= VOCABULARY_LIMIT
        assert sum(len(n) + len(s) for n, s in items) <= VOCABULARY_CHAR_LIMIT

    def test_format_returns_empty_for_empty_vocabulary(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        empty = tmp_path / "taxonomy.yaml"
        empty.write_text("version: 1\n", encoding="utf-8")
        monkeypatch.setattr(taxonomy_module, "TAXONOMY_FILE", empty)
        load_taxonomy.cache_clear()
        assert format_vocabulary() == ""

    def test_format_renders_bullets(self):
        rendered = format_vocabulary()
        assert rendered.startswith("- ")
        assert "多Agent协同" in rendered
