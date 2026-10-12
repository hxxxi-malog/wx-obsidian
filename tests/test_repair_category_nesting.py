"""repair_category_nesting 测试：分类折叠规则与链接清理（不触碰真实 vault）。"""

from __future__ import annotations

from pathlib import Path

from repair_category_nesting import (
    build_plan,
    canonical_category,
    canonical_link_target,
    clean_moc_entries,
    dissolve_self_folders,
    is_nested,
    rewrite_text_links,
    scan_residual_links,
)


class TestCanonicalCategory:
    """折叠连续重复的路径段。"""

    def test_collapses_repeated_segments(self):
        assert canonical_category("Agent/RAG/RAG/RAG") == "Agent/RAG"
        assert canonical_category("Agent/上下文工程/上下文工程/上下文工程") == "Agent/上下文工程"
        assert canonical_category("A/B/B/B/B/B") == "A/B"

    def test_keeps_legit_paths_untouched(self):
        """合法的单层子目录与非连续重复不得被改写。"""
        for category in ("Agent/MCP", "LLM基础/推理优化", "A/B/A", "Agent", "Harness Engineering/Loop Engineering"):
            assert canonical_category(category) == category
            assert not is_nested(category)

    def test_handles_empty_segments(self):
        assert canonical_category("Agent//RAG") == "Agent/RAG"
        assert canonical_category("") == ""


class TestCanonicalLinkTarget:
    """链接目标的分类前缀折叠。"""

    def test_collapses_prefix_only(self):
        assert canonical_link_target("Agent/RAG/RAG/某文") == "Agent/RAG/某文"
        assert canonical_link_target("Agent/RAG/RAG/_MOC") == "Agent/RAG/_MOC"

    def test_keeps_anchor(self):
        """`#锚点` 挂在最后一段上，切分后必须原样保留。"""
        assert canonical_link_target("Agent/RAG/RAG/某文#小节") == "Agent/RAG/某文#小节"

    def test_noop_cases(self):
        for target in ("", "常识", "Agent/MCP/正常文", "Agent/MCP/正常文#小节", "#同页锚点"):
            assert canonical_link_target(target) is None


class TestRewriteTextLinks:
    """正文链接改写。"""

    def test_rewrites_only_nested(self):
        text = "见 [[Agent/RAG/RAG/RAG/某文|某文]] 与 [[Agent/MCP/正常文]]，还有 [[常识]]"
        new_text, changed = rewrite_text_links(text)
        assert changed == 1
        assert new_text == "见 [[Agent/RAG/某文|某文]] 与 [[Agent/MCP/正常文]]，还有 [[常识]]"

    def test_rewrites_anchor_links(self):
        new_text, changed = rewrite_text_links("[[Harness Engineering/Loop Engineering/Loop Engineering/文#节]]")
        assert changed == 1
        assert new_text == "[[Harness Engineering/Loop Engineering/文#节]]"


class TestDissolveSelfFolders:
    """指向自己的文件夹组要拆开，指向合法子目录的要保留。"""

    def test_dissolves_self_and_keeps_other(self):
        moc = (
            "# 多Agent协同\n\n"
            "- 2026-01-01 [[Agent/多Agent协同/独立文|独立文]]\n"
            "- 📁 [[Agent/多Agent协同/_MOC|多Agent协同]]\n"
            "  - 2026-02-02 [[Agent/多Agent协同/组内文|组内文]]\n"
            "- 📁 [[Agent/多Agent协同/合法子目录/_MOC|合法子目录]]\n"
            "  - 2026-03-03 [[Agent/多Agent协同/合法子目录/子文|子文]]\n"
        )
        new_text, dissolved = dissolve_self_folders(moc, "Agent/多Agent协同")
        assert dissolved == 1
        assert "[[Agent/多Agent协同/_MOC" not in new_text
        assert "[[Agent/多Agent协同/合法子目录/_MOC" in new_text
        assert "[[Agent/多Agent协同/组内文|组内文]]" in new_text

    def test_noop_without_self_folder(self):
        moc = "# A\n\n- 2026-01-01 [[A/文|文]]\n"
        new_text, dissolved = dissolve_self_folders(moc, "A")
        assert dissolved == 0
        assert new_text == moc


class TestCleanMocEntries:
    """同一 MOC 内同一目标只保留一条。"""

    def test_dedupes_standalone(self):
        moc = "# RAG\n\n- 2026-07-05 [[Agent/RAG/文|文]]\n- 2026-07-08 [[Agent/RAG/文|文]]\n"
        new_text, removed = clean_moc_entries(moc)
        assert removed == 1
        assert new_text.count("[[Agent/RAG/文|文]]") == 1

    def test_drops_standalone_already_in_folder_group(self):
        moc = (
            "# Agent\n\n"
            "- 2026-01-01 [[Agent/RAG/文|文]]\n"
            "- 📁 [[Agent/RAG/_MOC|RAG]]\n"
            "  - 2026-01-02 [[Agent/RAG/文|文]]\n"
        )
        new_text, removed = clean_moc_entries(moc)
        assert removed == 1
        assert new_text.count("[[Agent/RAG/文|文]]") == 1

    def test_noop_when_unique(self):
        moc = "# RAG\n\n- 2026-07-05 [[Agent/RAG/甲|甲]]\n- 2026-07-08 [[Agent/RAG/乙|乙]]\n"
        assert clean_moc_entries(moc) == (moc, 0)


class TestBuildPlan:
    """在合成 vault 上验证计划（不写盘）。"""

    def _make_vault(self, tmp_path: Path) -> Path:
        articles_dir = tmp_path / "公众号文章"
        nested = articles_dir / "Agent" / "上下文工程" / "上下文工程" / "上下文工程"
        nested.mkdir(parents=True)
        for i in range(3):
            (nested / f"文{i}.md").write_text(
                '---\ncategory: "Agent/上下文工程/上下文工程/上下文工程"\n---\n', encoding="utf-8"
            )
        (nested / "_MOC.md").write_text("# 上下文工程\n\n", encoding="utf-8")
        (articles_dir / "Agent" / "上下文工程").mkdir(parents=True, exist_ok=True)
        (articles_dir / "Agent" / "MCP").mkdir(parents=True, exist_ok=True)
        (articles_dir / "Agent" / "MCP" / "正常.md").write_text('---\ncategory: "Agent/MCP"\n---\n', encoding="utf-8")
        return articles_dir

    def test_plan_collapses_and_fixes_records(self, tmp_path: Path):
        articles_dir = self._make_vault(tmp_path)
        nested = articles_dir / "Agent" / "上下文工程" / "上下文工程" / "上下文工程"
        processed = {
            "a1": {
                "status": "done",
                "category": "Agent/上下文工程/上下文工程/上下文工程",
                "file": str(nested / "文1.md"),
            },
            "a2": {"status": "done", "category": "Agent/MCP", "file": str(articles_dir / "Agent" / "MCP" / "正常.md")},
        }
        plan = build_plan(articles_dir, processed, ["Agent/MCP", "Agent/上下文工程/上下文工程"])

        assert [m.new_category for m in plan.file_moves] == ["Agent/上下文工程"] * 3
        assert len(plan.moc_merges) == 1
        assert plan.dirs_to_remove == [nested, nested.parent]
        assert plan.categories_to_fix == [("Agent/上下文工程/上下文工程", "Agent/上下文工程")]
        # 只修嵌套记录，合法记录不进入计划
        assert [f.article_id for f in plan.record_fixes] == ["a1"]
        assert plan.record_fixes[0].by_path is True

    def test_scan_residual_links_finds_duplicates_and_nested_links(self, tmp_path: Path):
        articles_dir = tmp_path / "公众号文章"
        cat = articles_dir / "Agent" / "RAG"
        cat.mkdir(parents=True)
        (cat / "_MOC.md").write_text(
            "# RAG\n\n"
            "- 2026-07-05 [[Agent/RAG/RAG/RAG/文|文]]\n"
            "- 2026-07-05 [[Agent/RAG/文|文]]\n",
            encoding="utf-8",
        )
        result = scan_residual_links(articles_dir)
        assert len(result.files) == 1
        assert result.link_total == 1
        assert result.deduped_total == 1
