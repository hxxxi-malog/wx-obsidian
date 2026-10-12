"""repair_category_nesting 测试：分类折叠规则与链接清理（不触碰真实 vault）。"""

from __future__ import annotations

from pathlib import Path

from repair_category_nesting import (
    apply_plan,
    build_plan,
    canonical_category,
    canonical_link_target,
    clean_moc_entries,
    dissolve_self_folders,
    is_nested,
    rewrite_text_links,
    scan_residual_links,
)
from wx_obsidian.config import load_processed, save_processed
from wx_obsidian.config_manager import ConfigManager


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

    def test_retargets_category_prefix_independent_of_title_form(self):
        """回归：分类合并后的链接重定向必须按「前缀」改。

        `_fix_links_batch` 会把标题重新 sanitize 一次，遇到文件名里带 `：`（历史文件）
        的笔记就匹配不上，于是 20 个文件的 related_topics 在合并后断链。
        """
        prefix_map = {
            "Agent/Loop工程": "Harness Engineering/Loop Engineering",
            "Harness Engineering/多Agent协同": "Agent/多Agent协同",
        }
        text = "见 [[Agent/Loop工程/甲：乙|甲：乙]] 与 [[Harness Engineering/多Agent协同/丙]]"
        new_text, changed = rewrite_text_links(text, prefix_map)
        assert changed == 2
        assert new_text == (
            "见 [[Harness Engineering/Loop Engineering/甲：乙|甲：乙]] 与 [[Agent/多Agent协同/丙]]"
        )

    def test_prefix_map_ignores_identity_and_prefers_longest(self):
        text = "[[A/B/C/文]] 与 [[A/B/文]]"
        new_text, changed = rewrite_text_links(
            text, {"A/B": "X", "A/B/C": "Y", "X": "X"}
        )
        assert changed == 2
        assert new_text == "[[Y/文]] 与 [[X/文]]"


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


class TestLegacyCategoryMismatch:
    """分类名与文件位置对不上的历史遗留。"""

    def _record(self, category: str, path: Path, status: str = "done") -> dict[str, object]:
        return {
            "status": status,
            "category": category,
            "sub_topic": category.rsplit("/", 1)[-1],
            "title": path.stem,
            "file": str(path),
            "processed_at": "2026-07-08T15:12:07",
        }

    def test_moves_file_into_existing_subdir_when_it_waits_in_parent(self, tmp_path: Path):
        """文件还在父目录、目标子目录已存在 → 补完当年没做成的迁移。"""
        articles_dir = tmp_path / "公众号文章"
        parent = articles_dir / "A"
        parent.mkdir(parents=True)
        (parent / "B").mkdir()
        src = parent / "文.md"
        src.write_text('---\ncategory: "A/B"\n---\n', encoding="utf-8")

        plan = build_plan(articles_dir, {"a1": self._record("A/B", src)}, [])

        assert len(plan.legacy_fixes) == 1
        fix = plan.legacy_fixes[0]
        assert fix.dst == parent / "B" / "文.md"
        assert fix.new_category == "A/B"

    def test_renames_only_when_subdir_never_existed(self, tmp_path: Path):
        """子目录从未建过 → 只把分类名对齐到实际所在层，不造空壳目录。"""
        articles_dir = tmp_path / "公众号文章"
        parent = articles_dir / "A"
        parent.mkdir(parents=True)
        src = parent / "文.md"
        src.write_text('---\ncategory: "A/从未存在"\n---\n', encoding="utf-8")

        plan = build_plan(articles_dir, {"a1": self._record("A/从未存在", src)}, [])

        assert len(plan.legacy_fixes) == 1
        fix = plan.legacy_fixes[0]
        assert fix.dst is None
        assert fix.new_category == "A"
        assert not (parent / "从未存在").exists()

    def test_never_moves_between_two_existing_subdirs(self, tmp_path: Path):
        """回归：文件在「另一个同级子目录」里时，绝不能把它搬去记录声称的那个。

        Loop工程 / Loop Engineering 就是这种双名目录；搬动会清空一侧、混淆另一侧。
        """
        articles_dir = tmp_path / "公众号文章"
        parent = articles_dir / "A"
        (parent / "中文名").mkdir(parents=True)
        (parent / "English").mkdir()
        src = parent / "中文名" / "文.md"
        src.write_text('---\ncategory: "A/English"\n---\n', encoding="utf-8")

        plan = build_plan(articles_dir, {"a1": self._record("A/English", src)}, [])

        fix = plan.legacy_fixes[0]
        assert fix.dst is None, "不得跨目录搬文件"
        assert fix.new_category == "A/中文名"

    def test_aligns_category_when_file_is_deeper_than_category(self, tmp_path: Path):
        """镜像情形：category 只写了父分类，文件却在子目录里 → 对齐到子目录。

        不对齐的话，管线会往父 MOC 里写一条指向 [[Agent/标题]] 的断链。
        """
        articles_dir = tmp_path / "公众号文章"
        sub = articles_dir / "Agent" / "上下文工程"
        sub.mkdir(parents=True)
        src = sub / "文.md"
        src.write_text('---\ncategory: "Agent"\n---\n', encoding="utf-8")

        plan = build_plan(articles_dir, {"a1": self._record("Agent", src)}, [])

        fix = plan.legacy_fixes[0]
        assert fix.dst is None
        assert fix.new_category == "Agent/上下文工程"

    def test_skips_consistent_and_non_done_records(self, tmp_path: Path):
        articles_dir = tmp_path / "公众号文章"
        sub = articles_dir / "A" / "B"
        sub.mkdir(parents=True)
        ok = sub / "一致.md"
        ok.write_text('---\ncategory: "A/B"\n---\n', encoding="utf-8")
        skipped = articles_dir / "A" / "跳过.md"
        skipped.write_text('---\ncategory: "A/B"\n---\n', encoding="utf-8")

        plan = build_plan(
            articles_dir,
            {"ok": self._record("A/B", ok), "bad": self._record("A/B", skipped, status="error")},
            [],
        )
        assert plan.legacy_fixes == []


class TestMissingMoc:
    """有文章却没有 _MOC.md 的目录。"""

    def test_detects_dir_without_moc(self, tmp_path: Path):
        articles_dir = tmp_path / "公众号文章"
        cat = articles_dir / "Agent" / "MCP"
        cat.mkdir(parents=True)
        for name in ("甲", "乙"):
            path = cat / f"{name}.md"
            path.write_text('---\ncategory: "Agent/MCP"\n---\n', encoding="utf-8")
        with_moc = articles_dir / "Agent" / "RAG"
        with_moc.mkdir()
        (with_moc / "文.md").write_text('---\ncategory: "Agent/RAG"\n---\n', encoding="utf-8")
        (with_moc / "_MOC.md").write_text("# RAG\n\n", encoding="utf-8")

        processed = {
            "m1": {
                "status": "done",
                "category": "Agent/MCP",
                "file": str(cat / "甲.md"),
                "title": "甲",
                "processed_at": "2026-01-02T00:00:00",
            },
            "m2": {
                "status": "done",
                "category": "Agent/MCP",
                "file": str(cat / "乙.md"),
                "title": "乙",
                "processed_at": "2026-01-01T00:00:00",
            },
            "r1": {
                "status": "done",
                "category": "Agent/RAG",
                "file": str(with_moc / "文.md"),
                "title": "文",
                "processed_at": "2026-01-01T00:00:00",
            },
        }
        plan = build_plan(articles_dir, processed, [])

        assert [m.category for m in plan.missing_mocs] == ["Agent/MCP"]
        assert [e[1] for e in plan.missing_mocs[0].entries] == ["乙", "甲"]  # 按日期排序

    def test_counts_file_moved_in_by_legacy_fix(self, tmp_path: Path):
        """搬进子目录的文件也要计入新 MOC，否则索引会漏。"""
        articles_dir = tmp_path / "公众号文章"
        parent = articles_dir / "A"
        (parent / "B").mkdir(parents=True)
        src = parent / "等待迁移.md"
        src.write_text('---\ncategory: "A/B"\n---\n', encoding="utf-8")

        plan = build_plan(articles_dir, {"a1": self._record_for(src)}, [])

        assert [m.category for m in plan.missing_mocs] == ["A/B"]
        assert len(plan.missing_mocs[0].entries) == 1

    def _record_for(self, path: Path) -> dict[str, object]:
        return {
            "status": "done",
            "category": "A/B",
            "title": path.stem,
            "file": str(path),
            "processed_at": "2026-01-01T00:00:00",
        }


class TestCategoryMerge:
    """L0 声明式分类合并：plan 与 apply 都要可验证。"""

    def _build_vault(self, tmp_path: Path) -> tuple[Path, dict[str, dict[str, object]]]:
        articles_dir = tmp_path / "公众号文章"
        src_a = articles_dir / "Agent" / "Loop工程"
        src_b = articles_dir / "Harness Engineering" / "Loop工程"
        dst = articles_dir / "Harness Engineering" / "Loop Engineering"
        for directory in (src_a, src_b, dst, articles_dir / "Agent", articles_dir / "Harness Engineering"):
            directory.mkdir(parents=True, exist_ok=True)

        (articles_dir / "Agent" / "_MOC.md").write_text(
            "# Agent\n\n- 📁 [[Agent/Loop工程/_MOC|Loop工程]]\n"
            "  - 2026-06-14 [[Agent/Loop工程/甲|甲]]\n",
            encoding="utf-8",
        )
        (articles_dir / "Harness Engineering" / "_MOC.md").write_text(
            "# Harness Engineering\n\n"
            "- 📁 [[Harness Engineering/Loop Engineering/_MOC|Loop Engineering]]\n"
            "  - 2026-06-23 [[Harness Engineering/Loop Engineering/丙|丙]]\n"
            "- 📁 [[Harness Engineering/Loop工程/_MOC|Loop工程]]\n"
            "  - 2026-06-11 [[Harness Engineering/Loop工程/乙|乙]]\n",
            encoding="utf-8",
        )

        processed: dict[str, dict[str, object]] = {}
        for index, (directory, title, sub_topic) in enumerate(
            [
                (src_a, "甲", "Loop工程"),
                (src_b, "乙", "Loop工程"),
                (dst, "丙", "Loop Engineering"),
            ]
        ):
            path = directory / f"{title}.md"
            path.write_text(
                f'---\ntitle: "{title}"\ncategory: "{directory.relative_to(articles_dir).as_posix()}"\n'
                f'sub_topic: "{sub_topic}"\n---\n\n见 [[Agent/Loop工程/{title}|{title}]]\n',
                encoding="utf-8",
            )
            processed[f"a{index}"] = {
                "status": "done",
                "category": directory.relative_to(articles_dir).as_posix(),
                "sub_topic": sub_topic,
                "title": title,
                "file": str(path),
                "processed_at": "2026-06-20T00:00:00",
            }

        (src_a / "_MOC.md").write_text(
            "# Loop工程\n\n- 2026-06-14 [[Agent/Loop工程/甲|甲]]\n", encoding="utf-8"
        )
        (src_b / "_MOC.md").write_text(
            "# Loop工程\n\n- 2026-06-11 [[Harness Engineering/Loop工程/乙|乙]]\n",
            encoding="utf-8",
        )
        (dst / "_MOC.md").write_text(
            "# Loop Engineering\n\n"
            "- 2026-06-23 [[Harness Engineering/Loop Engineering/丙|丙]]\n",
            encoding="utf-8",
        )
        return articles_dir, processed

    def test_plan_pairs_sources_to_destination(self, tmp_path: Path):
        articles_dir, processed = self._build_vault(tmp_path)
        plan = build_plan(articles_dir, processed, [])

        assert ("Agent/Loop工程", "Harness Engineering/Loop Engineering") in plan.merges
        assert ("Harness Engineering/Loop工程", "Harness Engineering/Loop Engineering") in plan.merges
        moved = {m.src.name for m in plan.file_moves}
        assert moved == {"甲.md", "乙.md"}
        assert {m.new_category for m in plan.file_moves} == {"Harness Engineering/Loop Engineering"}
        # 源目录进清理列表；父 MOC 组要删，目标组要重建
        assert articles_dir / "Agent" / "Loop工程" in plan.dirs_to_remove
        assert "Agent/Loop工程" in plan.merge_parent_cleanups
        assert "Harness Engineering/Loop Engineering" in plan.merge_parent_refreshes

    def test_apply_merges_files_mocs_and_records(self, tmp_path: Path):
        articles_dir, processed = self._build_vault(tmp_path)
        dst = articles_dir / "Harness Engineering" / "Loop Engineering"
        cm = ConfigManager(config_dir=tmp_path / "cfg")
        cm.load()
        cm.set("categories", ["Agent", "Agent/Loop工程", "Harness Engineering", "Harness Engineering/Loop Engineering"])
        cm.save()
        save_processed(processed)

        plan = build_plan(articles_dir, processed, ["Agent/Loop工程"])
        apply_plan(plan, articles_dir, cm, tmp_path / "processed.json")

        # 文件已并入目标目录，源目录删除
        assert {p.name for p in dst.glob("*.md")} == {"_MOC.md", "丙.md", "甲.md", "乙.md"}
        assert not (articles_dir / "Agent" / "Loop工程").exists()
        assert not (articles_dir / "Harness Engineering" / "Loop工程").exists()

        # frontmatter：category 与 sub_topic 都已更新
        moved = (dst / "甲.md").read_text(encoding="utf-8")
        assert 'category: "Harness Engineering/Loop Engineering"' in moved
        assert 'sub_topic: "Loop Engineering"' in moved

        # MOC：条目并入且前缀已改写、按目标去重
        moc = (dst / "_MOC.md").read_text(encoding="utf-8")
        assert "[[Harness Engineering/Loop Engineering/甲|甲]]" in moc
        assert "[[Harness Engineering/Loop Engineering/乙|乙]]" in moc
        assert "[[Agent/Loop工程/" not in moc

        # 父 MOC：源组删除、目标组重建
        agent_moc = (articles_dir / "Agent" / "_MOC.md").read_text(encoding="utf-8")
        assert "[[Agent/Loop工程/_MOC" not in agent_moc
        he_moc = (articles_dir / "Harness Engineering" / "_MOC.md").read_text(encoding="utf-8")
        assert "[[Harness Engineering/Loop工程/_MOC" not in he_moc
        assert "[[Harness Engineering/Loop Engineering/甲|甲]]" in he_moc

        # processed.json：file 与 category 都跟着落点走
        reloaded = load_processed()
        assert reloaded["a0"]["category"] == "Harness Engineering/Loop Engineering"
        assert reloaded["a0"]["file"] == str(dst / "甲.md")
        assert reloaded["a0"]["sub_topic"] == "Loop Engineering"

        # config：源分类移除、目标分类保留
        assert cm.get("categories") == ["Agent", "Harness Engineering", "Harness Engineering/Loop Engineering"]

    def test_second_run_is_noop(self, tmp_path: Path):
        articles_dir, processed = self._build_vault(tmp_path)
        cm = ConfigManager(config_dir=tmp_path / "cfg")
        cm.load()
        save_processed(processed)
        apply_plan(build_plan(articles_dir, processed, []), articles_dir, cm, tmp_path / "p.json")

        again = build_plan(articles_dir, load_processed(), [])
        assert again.merges == []
        assert again.file_moves == []
