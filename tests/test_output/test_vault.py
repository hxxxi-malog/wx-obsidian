"""output/vault.py 测试：概念页面、MOC、分类管理、子目录拆分。"""

from __future__ import annotations

from wx_obsidian.output.vault import (
    _count_sub_topic_articles,
    _insert_into_folder_group,
    _parse_moc_content,
    _rebuild_moc,
    ensure_category,
    ensure_concept_page,
    maybe_create_subcategory,
    update_moc,
)


class TestEnsureConceptPage:
    """ensure_concept_page 测试。"""

    def test_creates_new_concept(self, tmp_vault):
        """创建新概念页面。"""
        vault_path, articles_dir = tmp_vault
        ensure_concept_page(vault_path, "Self-Attention", "自注意力机制的定义", articles_dir)

        concept_file = articles_dir / "概念" / "Self-Attention.md"
        assert concept_file.exists()
        content = concept_file.read_text(encoding="utf-8")
        assert "# Self-Attention" in content
        assert "自注意力机制的定义" in content
        assert "tags: [概念]" in content

    def test_creates_concept_directory(self, tmp_vault):
        """概念目录不存在时自动创建。"""
        vault_path, articles_dir = tmp_vault
        concept_dir = articles_dir / "概念"
        # 确保目录不存在
        assert not concept_dir.exists()

        ensure_concept_page(vault_path, "测试概念", "描述", articles_dir)
        assert concept_dir.exists()

    def test_skips_existing_concept(self, tmp_vault):
        """已存在的概念不覆盖。"""
        vault_path, articles_dir = tmp_vault
        concept_dir = articles_dir / "概念"
        concept_dir.mkdir(parents=True)
        concept_file = concept_dir / "已有概念.md"
        original_content = "# 原始内容"
        concept_file.write_text(original_content, encoding="utf-8")

        ensure_concept_page(vault_path, "已有概念", "新描述", articles_dir)
        assert concept_file.read_text(encoding="utf-8") == original_content


class TestUpdateMoc:
    """update_moc 测试。"""

    def test_appends_article_link(self, tmp_vault):
        """追加文章链接到 MOC。"""
        vault_path, articles_dir = tmp_vault
        category_dir = articles_dir / "LLM"
        category_dir.mkdir()
        moc_file = category_dir / "_MOC.md"
        moc_file.write_text("# LLM\n\n", encoding="utf-8")

        update_moc(vault_path, "LLM", "新文章", "2026-01-01", articles_dir)

        content = moc_file.read_text(encoding="utf-8")
        assert "- 2026-01-01 [[LLM/新文章|新文章]]" in content

    def test_no_duplicate_entry(self, tmp_vault):
        """重复文章不重复添加。"""
        vault_path, articles_dir = tmp_vault
        category_dir = articles_dir / "LLM"
        category_dir.mkdir()
        moc_file = category_dir / "_MOC.md"
        moc_file.write_text("# LLM\n\n- 2026-01-01 [[LLM/文章|文章]]\n", encoding="utf-8")

        update_moc(vault_path, "LLM", "文章", "2026-01-01", articles_dir)

        content = moc_file.read_text(encoding="utf-8")
        assert content.count("[[LLM/文章|文章]]") == 1

    def test_missing_moc_file(self, tmp_vault):
        """MOC 文件不存在时自动创建。"""
        vault_path, articles_dir = tmp_vault
        category_dir = articles_dir / "LLM"
        category_dir.mkdir()
        # 不创建 _MOC.md

        # 不应抛异常，并补建 MOC
        update_moc(vault_path, "LLM", "文章", "2026-01-01", articles_dir)
        assert (category_dir / "_MOC.md").exists()


class TestEnsureCategory:
    """ensure_category 测试。"""

    def test_creates_new_category(self, tmp_vault):
        """创建新分类目录和 MOC。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM", "其他"]}

        ensure_category(vault_path, config, "新分类", articles_dir)

        assert "新分类" in config["categories"]
        assert (articles_dir / "新分类").exists()
        assert (articles_dir / "新分类" / "_MOC.md").exists()

    def test_existing_category_noop(self, tmp_vault):
        """已有分类不做任何操作。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM", "其他"]}
        original_len = len(config["categories"])

        ensure_category(vault_path, config, "LLM", articles_dir)
        assert len(config["categories"]) == original_len

    def test_updates_root_moc(self, tmp_vault):
        """新分类追加到根 MOC。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM"]}
        root_moc = articles_dir / "_MOC.md"
        root_moc.write_text("# 公众号文章\n\n- [[LLM]]\n", encoding="utf-8")

        ensure_category(vault_path, config, "新分类", articles_dir)

        content = root_moc.read_text(encoding="utf-8")
        assert "新分类/_MOC" in content


class TestCountSubTopicArticles:
    """_count_sub_topic_articles 测试。"""

    def test_count_matching(self, sample_processed: dict):
        """统计匹配的子主题文章。"""
        count = _count_sub_topic_articles(sample_processed, "LLM", "注意力机制")
        assert count == 2

    def test_count_no_match(self, sample_processed: dict):
        """无匹配时返回 0。"""
        count = _count_sub_topic_articles(sample_processed, "RAG", "向量检索")
        assert count == 0

    def test_ignores_non_done(self, sample_processed: dict):
        """忽略非 done 状态的记录。"""
        # sample_processed 中 article_003 是 skipped
        count = _count_sub_topic_articles(sample_processed, "LLM", "注意力机制")
        assert count == 2  # 只计算 done 的

    def test_counts_migrated_articles(self):
        """已迁移到子目录（category 为 分类/子主题）的文章也计入。"""
        processed = {
            "a": {"status": "done", "category": "LLM", "sub_topic": "注意力机制"},
            "b": {"status": "done", "category": "LLM/注意力机制", "sub_topic": "注意力机制"},
            "c": {"status": "done", "category": "LLM", "sub_topic": "其他主题"},
        }
        count = _count_sub_topic_articles(processed, "LLM", "注意力机制")
        assert count == 2

    def test_ignores_non_dict_records(self):
        """非 dict 的历史记录不计入。"""
        processed = {"a": "legacy-string", "b": None}
        assert _count_sub_topic_articles(processed, "LLM", "注意力机制") == 0


class TestMaybeCreateSubcategory:
    """maybe_create_subcategory 测试。"""

    def test_creates_subcategory_at_threshold(self, tmp_vault, monkeypatch):
        """达到阈值时创建子目录，并立即持久化 processed。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM"], "obsidian": {"articles_dir": "公众号文章"}}

        # 重定向持久化，避免写入真实 ~/.wx-obsidian/processed.json
        persisted: list[dict] = []
        monkeypatch.setattr(
            "wx_obsidian.output.vault.save_processed",
            lambda processed: persisted.append(processed),
        )

        # 创建分类目录和文章文件
        llm_dir = articles_dir / "LLM"
        llm_dir.mkdir()
        for i in range(3):
            (llm_dir / f"文章{i}.md").write_text(f"# 文章{i}", encoding="utf-8")

        processed = {
            f"id{i}": {
                "title": f"文章{i}",
                "status": "done",
                "category": "LLM",
                "sub_topic": "注意力机制",
                "file": str(llm_dir / f"文章{i}.md"),
                "processed_at": "2026-01-01T00:00:00",
            }
            for i in range(3)
        }

        maybe_create_subcategory(vault_path, config, processed, "LLM", "注意力机制")

        sub_dir = articles_dir / "LLM" / "注意力机制"
        assert sub_dir.exists()
        assert (sub_dir / "_MOC.md").exists()
        # 文件已迁移，processed 记录指向新路径且 category 已更新
        assert all(record["category"] == "LLM/注意力机制" for record in processed.values())
        assert all((sub_dir / f"文章{i}.md").exists() for i in range(3))
        assert persisted == [processed]

    def test_no_creation_below_threshold(self, tmp_vault):
        """未达阈值时不创建子目录。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM"], "obsidian": {"articles_dir": "公众号文章"}}

        llm_dir = articles_dir / "LLM"
        llm_dir.mkdir()

        processed = {
            "id1": {
                "title": "文章1",
                "status": "done",
                "category": "LLM",
                "sub_topic": "注意力机制",
                "file": str(llm_dir / "文章1.md"),
            }
        }

        maybe_create_subcategory(vault_path, config, processed, "LLM", "注意力机制")
        assert not (articles_dir / "LLM" / "注意力机制").exists()

    def test_empty_sub_topic_noop(self, tmp_vault):
        """空 sub_topic 不做任何操作。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM"], "obsidian": {"articles_dir": "公众号文章"}}
        maybe_create_subcategory(vault_path, config, {}, "LLM", "")
        assert list(articles_dir.iterdir()) == []

    def test_no_nesting_when_already_migrated(self, tmp_vault):
        """category 末段已是 sub_topic 时不再嵌套创建子目录。"""
        vault_path, articles_dir = tmp_vault
        config = {"categories": ["LLM"], "obsidian": {"articles_dir": "公众号文章"}}
        processed = {
            f"id{i}": {
                "title": f"文章{i}",
                "status": "done",
                "category": "LLM/注意力机制",
                "sub_topic": "注意力机制",
                "file": str(articles_dir / "LLM" / "注意力机制" / f"文章{i}.md"),
            }
            for i in range(3)
        }

        maybe_create_subcategory(vault_path, config, processed, "LLM/注意力机制", "注意力机制")

        assert not (articles_dir / "LLM" / "注意力机制" / "注意力机制").exists()


class TestParseMocContent:
    """_parse_moc_content 测试。"""

    def test_parses_title_and_articles(self):
        """解析标题和普通文章条目。"""
        content = "# LLM\n\n- 2026-01-01 [[LLM/文章A]]\n- 2026-01-02 [[LLM/文章B]]\n"
        title, folders, standalone = _parse_moc_content(content)
        assert title == "# LLM"
        assert folders == []
        assert len(standalone) == 2

    def test_parses_folder_groups(self):
        """解析文件夹组及其子文章。"""
        content = (
            "# Agent\n\n"
            "- 📁 [[Agent/RAG/_MOC|RAG]]\n"
            "  - 2026-01-01 [[Agent/RAG/文章A]]\n"
            "  - 2026-01-02 [[Agent/RAG/文章B]]\n"
        )
        title, folders, standalone = _parse_moc_content(content)
        assert title == "# Agent"
        assert len(folders) == 1
        assert "RAG" in folders[0][0]
        assert len(folders[0][1]) == 2
        assert standalone == []

    def test_mixed_standalone_and_folders(self):
        """同时包含独立条目和文件夹组。"""
        content = (
            "# Agent\n\n"
            "- 2026-01-01 [[Agent/独立文章]]\n"
            "- 📁 [[Agent/RAG/_MOC|RAG]]\n"
            "  - 2026-01-02 [[Agent/RAG/文章A]]\n"
        )
        _, folders, standalone = _parse_moc_content(content)
        assert len(standalone) == 1
        assert len(folders) == 1

    def test_empty_content(self):
        """空内容返回默认标题。"""
        title, folders, standalone = _parse_moc_content("")
        assert title == "# (untitled)"
        assert folders == []
        assert standalone == []

    def test_no_title(self):
        """无标题行时返回默认标题。"""
        content = "- 2026-01-01 [[文章]]\n"
        title, _, _ = _parse_moc_content(content)
        assert title == "# (untitled)"


class TestInsertIntoFolderGroup:
    """_insert_into_folder_group 测试。"""

    def test_inserts_by_date_order(self):
        """按日期倒序插入条目。"""
        folder_line = "- 📁 [[Agent/RAG/_MOC|RAG]]"
        folders: list[tuple[str, list[str]]] = [
            (folder_line, ["- 2026-01-03 [[旧文章C]]", "- 2026-01-01 [[旧文章A]]"])
        ]
        result = _insert_into_folder_group(folders, "RAG", "- 2026-01-02 [[新文章]]")
        assert result is True
        assert folders[0][1][1] == "- 2026-01-02 [[新文章]]"

    def test_inserts_newest_first(self):
        """最新日期插入到最前面。"""
        folder_line = "- 📁 [[Agent/RAG/_MOC|RAG]]"
        folders: list[tuple[str, list[str]]] = [(folder_line, ["- 2026-01-01 [[旧文章]]"])]
        result = _insert_into_folder_group(folders, "RAG", "- 2026-01-05 [[新文章]]")
        assert result is True
        assert folders[0][1][0] == "- 2026-01-05 [[新文章]]"

    def test_returns_false_when_not_found(self):
        """未找到匹配文件夹时返回 False。"""
        folder_line = "- 📁 [[Agent/RAG/_MOC|RAG]]"
        folders: list[tuple[str, list[str]]] = [(folder_line, [])]
        result = _insert_into_folder_group(folders, "不存在", "- 2026-01-01 [[文章]]")
        assert result is False

    def test_no_false_positive_substring_match(self):
        """子串不应匹配到其他文件夹。"""
        folders: list[tuple[str, list[str]]] = [
            ("- 📁 [[Agent/Advanced RAG/_MOC|Advanced RAG]]", ["- 2026-01-01 [[旧文章]]"]),
            ("- 📁 [[Agent/RAG/_MOC|RAG]]", []),
        ]
        result = _insert_into_folder_group(folders, "RAG", "- 2026-01-02 [[新文章]]")
        assert result is True
        # 应插入到 RAG 文件夹（index 1），而非 Advanced RAG（index 0）
        assert len(folders[0][1]) == 1  # Advanced RAG 未变
        assert len(folders[1][1]) == 1  # RAG 已插入


class TestRebuildMoc:
    """_rebuild_moc 测试。"""

    def test_roundtrip_standalone_after_folder(self):
        """parse → rebuild → parse：独立条目在文件夹之后时保持独立。"""
        content = (
            "# Agent\n\n"
            "- 📁 [[Agent/RAG/_MOC|RAG]]\n"
            "  - 2026-01-02 [[Agent/RAG/文章A]]\n"
            "- 2026-01-01 [[Agent/独立文章]]\n"
        )
        title, folders, standalone = _parse_moc_content(content)
        rebuilt = _rebuild_moc(title, folders, standalone)
        title2, folders2, standalone2 = _parse_moc_content(rebuilt)
        assert title == title2
        assert len(folders) == len(folders2)
        assert len(standalone) == len(standalone2)
        assert folders[0][1] == folders2[0][1]

    def test_roundtrip_preserves_all_entries(self):
        """parse → rebuild → parse：所有条目都被保留。"""
        content = (
            "# Agent\n\n"
            "- 2026-01-01 [[Agent/独立文章]]\n"
            "- 📁 [[Agent/RAG/_MOC|RAG]]\n"
            "  - 2026-01-02 [[Agent/RAG/文章A]]\n"
        )
        title, folders, standalone = _parse_moc_content(content)
        rebuilt = _rebuild_moc(title, folders, standalone)
        title2, folders2, standalone2 = _parse_moc_content(rebuilt)
        assert title == title2
        # 独立条目在文件夹前会被吸收为缩进行，但所有内容都保留
        all_articles_1 = [a for _, arts in folders for a in arts] + standalone
        all_articles_2 = [a for _, arts in folders2 for a in arts] + standalone2
        assert all_articles_1 == all_articles_2

    def test_preserves_folder_indentation(self):
        """文件夹下文章应有缩进。"""
        title = "# Test"
        folders = [("- 📁 [[Cat/Sub/_MOC|Sub]]", ["- 2026-01-01 [[Cat/Sub/文章]]"])]
        result = _rebuild_moc(title, folders, [])
        assert "  - 2026-01-01 [[Cat/Sub/文章]]" in result

    def test_standalone_not_indented(self):
        """独立条目不应有缩进。"""
        title = "# Test"
        standalone = ["- 2026-01-01 [[文章]]"]
        result = _rebuild_moc(title, [], standalone)
        assert "- 2026-01-01 [[文章]]" in result
        assert "  - 2026-01-01 [[文章]]" not in result
