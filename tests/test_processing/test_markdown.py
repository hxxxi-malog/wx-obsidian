"""processing/markdown.py 测试：Markdown 生成、frontmatter、图片清理。"""

from __future__ import annotations

from wx_obsidian.processing.markdown import (
    _build_frontmatter,
    generate_markdown,
    remove_non_cdn_images,
)


class TestBuildFrontmatter:
    """_build_frontmatter 测试。"""

    def test_normal_frontmatter(self):
        """正常生成 frontmatter。"""
        fm = _build_frontmatter(
            "测试标题",
            "测试公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            "LLM",
            "",
            ["AI", "测试"],
        )
        assert 'title: "测试标题"' in fm
        assert 'source: "测试公众号"' in fm
        assert 'author: "作者"' in fm
        assert "date: 2026-01-01" in fm
        assert 'category: "LLM"' in fm
        assert "[AI, 测试]" in fm
        assert fm.startswith("---")
        assert fm.endswith("---")

    def test_empty_sub_topic_omitted(self):
        """空 sub_topic 不出现在 frontmatter 中。"""
        fm = _build_frontmatter(
            "标题", "公众号", "作者", "2026-01-01", "https://example.com", "LLM", "", ["tag"]
        )
        assert "sub_topic" not in fm

    def test_non_empty_sub_topic_included(self):
        """非空 sub_topic 出现在 frontmatter 中。"""
        fm = _build_frontmatter(
            "标题",
            "公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            "LLM",
            "注意力机制",
            ["tag"],
        )
        assert 'sub_topic: "注意力机制"' in fm

    def test_title_with_quotes(self):
        """标题中的双引号被替换为单引号。"""
        fm = _build_frontmatter(
            '他说"你好"', "公众号", "作者", "2026-01-01", "https://example.com", "LLM", "", ["tag"]
        )
        # title 行中原始 "你好" 的双引号应被替换为单引号
        title_line = [line for line in fm.split("\n") if line.startswith("title:")][0]
        # 原始 "你好" 中的双引号被替换为单引号
        assert "你好" in title_line
        # 标题值中不应有未被替换的原始双引号（除了 YAML 的外层引号）
        inner = title_line.split('title: "')[1].rstrip('"')
        assert '"' not in inner

    def test_tags_with_spaces(self):
        """标签中的空格替换为下划线。"""
        fm = _build_frontmatter(
            "标题",
            "公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            "LLM",
            "",
            ["AI Agent", "深度 学习"],
        )
        assert "AI_Agent" in fm
        assert "深度_学习" in fm

    def test_missing_author_uses_account_name(self):
        """作者为空时使用公众号名。"""
        fm = _build_frontmatter(
            "标题", "公众号", "", "2026-01-01", "https://example.com", "LLM", "", ["tag"]
        )
        assert 'author: "公众号"' in fm


class TestGenerateMarkdown:
    """generate_markdown 测试。"""

    def test_normal_generation(self, sample_llm_response: dict):
        """正常生成完整 markdown。"""
        md = generate_markdown(
            "测试标题",
            "测试公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            sample_llm_response,
        )
        assert "---" in md
        assert "## 摘要" in md
        assert "## 核心观点" in md
        assert "## 一、核心概念与背景" in md
        assert "## 二、核心机制拆解" in md
        assert "## 关键概念" in md
        assert "## 相关主题" in md

    def test_valid_topics_filters_dead_links(self, sample_llm_response: dict):
        """valid_topics 过滤不存在的相关主题。"""
        # related_topics 包含 "Agent架构"，但 valid_topics 不包含
        md = generate_markdown(
            "测试标题",
            "测试公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            sample_llm_response,
            valid_topics=["LLM", "RAG"],
        )
        # "Agent架构" 不在 valid_topics 中，应被过滤
        assert "Agent架构" not in md

    def test_valid_topics_keeps_valid_links(self, sample_llm_response: dict):
        """valid_topics 保留存在的相关主题（相关主题统一渲染为 [[目标|显示名]]）。"""
        md = generate_markdown(
            "测试标题",
            "测试公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            sample_llm_response,
            valid_topics=["Agent架构", "LLM"],
        )
        assert "[[Agent架构|Agent架构]]" in md

    def test_valid_topics_none_keeps_all(self, sample_llm_response: dict):
        """valid_topics 为 None 时保留所有相关主题。"""
        md = generate_markdown(
            "测试标题",
            "测试公众号",
            "作者",
            "2026-01-01",
            "https://example.com",
            sample_llm_response,
            valid_topics=None,
        )
        assert "[[Agent架构|Agent架构]]" in md

    def test_empty_concepts(self):
        """空 concepts 列表。"""
        data = {
            "category": "其他",
            "summary": "摘要",
            "key_points": [],
            "concepts": [],
            "tags": [],
            "related_topics": [],
            "body_sections": [],
        }
        md = generate_markdown("标题", "公众号", "", "2026-01-01", "https://example.com", data)
        assert "## 关键概念" in md
        assert "## 相关主题" in md

    def test_concepts_missing_name_key(self):
        """concepts 缺少 name 键时不崩溃。"""
        data = {
            "category": "其他",
            "summary": "摘要",
            "key_points": [],
            "concepts": [{"description": "有描述无名称"}],
            "tags": [],
            "related_topics": [],
            "body_sections": [],
        }
        md = generate_markdown("标题", "公众号", "", "2026-01-01", "https://example.com", data)
        assert "[[未知概念]]" in md

    def test_concepts_missing_description(self):
        """concepts 缺少 description 键时不崩溃。"""
        data = {
            "category": "其他",
            "summary": "摘要",
            "key_points": [],
            "concepts": [{"name": "概念A"}],
            "tags": [],
            "related_topics": [],
            "body_sections": [],
        }
        md = generate_markdown("标题", "公众号", "", "2026-01-01", "https://example.com", data)
        assert "[[概念A]]" in md


class TestRemoveNonCdnImages:
    """remove_non_cdn_images 测试。"""

    def test_removes_non_cdn_images(self):
        """清除非 CDN 图片链接。"""
        md = "文本\n![图片](https://example.com/img.png)\n更多文本"
        result = remove_non_cdn_images(md)
        assert "example.com" not in result
        assert "更多文本" in result

    def test_keeps_cdn_images(self):
        """保留微信 CDN 图片链接。"""
        md = "文本\n![图片](https://mmbiz.qpic.cn/test.png)\n更多文本"
        result = remove_non_cdn_images(md)
        assert "mmbiz.qpic.cn" in result

    def test_mixed_images(self):
        """混合场景：CDN 保留，非 CDN 清除。"""
        md = "![CDN](https://mmbiz.qpic.cn/a.png)\n![非CDN](https://imgur.com/b.png)"
        result = remove_non_cdn_images(md)
        assert "mmbiz.qpic.cn" in result
        assert "imgur.com" not in result

    def test_no_images(self):
        """无图片时原样返回。"""
        md = "纯文本内容"
        assert remove_non_cdn_images(md) == md

    def test_empty_string(self):
        """空字符串返回空字符串。"""
        assert remove_non_cdn_images("") == ""
