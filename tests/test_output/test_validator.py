"""output/validator.py 测试：Markdown 格式校验与修复。"""

from __future__ import annotations

from wx_obsidian.output.validator import (
    _check_frontmatter,
    _check_tables,
    _check_unclosed_code_block,
    _check_wikilinks,
    _compress_blank_lines,
    _fix_split_table_separators,
    _remove_duplicate_title,
    validate_and_fix,
)


class TestValidateAndFix:
    """validate_and_fix 端到端测试。"""

    def test_valid_markdown_passes(self, sample_markdown: str):
        """格式正确的 markdown 无问题。"""
        _, issues = validate_and_fix(sample_markdown)
        # 可能有轻微问题（如空行压缩），但不应有严重错误
        # 检查没有 frontmatter 缺失等关键问题
        critical = [i for i in issues if "缺少 frontmatter" in i or "缺少字段" in i]
        assert len(critical) == 0

    def test_fixes_unclosed_code_block(self):
        """自动闭合未关闭的代码块。"""
        md = """---
title: "测试"
source: "公众号"
date: 2026-01-01
tags: [test]
category: "LLM"
---

## 代码示例
```python
print("hello")
"""
        fixed, issues = validate_and_fix(md)
        assert "```" in fixed.split("print(")[1]
        assert any("代码块未闭合" in i for i in issues)

    def test_fixes_unquoted_frontmatter(self):
        """检测 frontmatter 引号未闭合。"""
        md = """---
title: "未闭合的引号
source: "公众号"
date: 2026-01-01
tags: [test]
category: "LLM"
---

内容
"""
        _, issues = validate_and_fix(md)
        assert any("引号未闭合" in i for i in issues)

    def test_removes_duplicate_title(self):
        """删除正文中重复的顶级标题。"""
        md = """---
title: "文章标题"
source: "公众号"
date: 2026-01-01
tags: [test]
category: "LLM"
---

# 文章标题

## 摘要
内容
"""
        fixed, issues = validate_and_fix(md)
        assert "# 文章标题" not in fixed.split("---")[2]
        assert any("顶级标题" in i for i in issues)

    def test_concept_page_with_h1(self):
        """概念页面允许 # 标题。"""
        md = """---
tags: [概念]
---

# 概念名称

定义内容。
"""
        fixed, issues = validate_and_fix(md, is_concept=True)
        # 概念页面不应删除 # 标题
        assert "# 概念名称" in fixed

    def test_missing_frontmatter(self):
        """缺少 frontmatter 时报告问题。"""
        md = "没有 frontmatter 的内容"
        _, issues = validate_and_fix(md)
        assert any("缺少 frontmatter" in i for i in issues)

    def test_missing_required_fields(self):
        """缺少必需字段时报告问题。"""
        md = """---
title: "标题"
---

内容
"""
        _, issues = validate_and_fix(md)
        assert any("source" in i for i in issues)
        assert any("date" in i for i in issues)
        assert any("tags" in i for i in issues)


class TestCheckFrontmatter:
    """_check_frontmatter 测试。"""

    def test_valid_frontmatter(self):
        """正确 frontmatter 无问题。"""
        lines = [
            "---",
            'title: "标题"',
            'source: "公众号"',
            "date: 2026-01-01",
            "tags: [test]",
            'category: "LLM"',
            "---",
        ]
        _, issues = _check_frontmatter(lines, is_concept=False)
        assert len(issues) == 0

    def test_missing_start_marker(self):
        """缺少起始 --- 标记。"""
        lines = ['title: "标题"', "---"]
        _, issues = _check_frontmatter(lines, is_concept=False)
        assert any("缺少 frontmatter 起始标记" in i for i in issues)

    def test_missing_end_marker(self):
        """缺少结束 --- 标记。"""
        lines = ["---", 'title: "标题"', 'source: "公众号"']
        _, issues = _check_frontmatter(lines, is_concept=False)
        assert any("缺少结束标记" in i for i in issues)


class TestRemoveDuplicateTitle:
    """_remove_duplicate_title 测试。"""

    def test_removes_top_level_title(self):
        """删除正文中重复的顶级标题。"""
        lines = ["---", 'title: "标题"', "---", "", "# 正文标题", "", "## 小节"]
        fixed, issue = _remove_duplicate_title(lines)
        assert "# 正文标题" not in fixed
        assert issue is not None

    def test_keeps_second_level_headings(self):
        """二级标题不受影响。"""
        lines = ["---", 'title: "标题"', "---", "", "## 小节", "内容"]
        fixed, issue = _remove_duplicate_title(lines)
        assert fixed == lines
        assert issue is None


class TestCheckTables:
    """_check_tables 测试。"""

    def test_adds_blank_line_before_table(self):
        """表格前缺少空行时自动插入。"""
        lines = ["正文", "| 列1 | 列2 |", "| --- | --- |", "| 数据 | 数据 |"]
        fixed, issues = _check_tables(lines)
        assert fixed[0] == "正文"
        assert fixed[1] == ""
        assert any("缺少空行" in i for i in issues)

    def test_fixes_unclosed_table_row(self):
        """表格行未以 | 结尾时自动修复。"""
        lines = ["| 列1 | 列2"]
        fixed, issues = _check_tables(lines)
        assert fixed[0].endswith("|")
        assert any("未闭合" in i for i in issues)

    def test_removes_trailing_backslash(self):
        """移除行末多余反斜杠。"""
        lines = ["| 内容 \\ |"]
        fixed, issues = _check_tables(lines)
        assert "\\" not in fixed[0]


class TestCheckUnclosedCodeBlock:
    """_check_unclosed_code_block 测试。"""

    def test_appends_closing_fence_when_document_starts_with_fence(self):
        """文档以代码块开头且未闭合时，在末尾补全 ```。"""
        lines = ["```python", 'print("hello")']
        fixed, issues = _check_unclosed_code_block(lines)
        assert fixed[-1] == "```"
        assert any("代码块未闭合" in i for i in issues)


class TestFixSplitTableSeparators:
    """_fix_split_table_separators 测试。"""

    def test_merges_split_separator(self):
        """分隔行后跟单独的 --- 时合并为完整分隔行。"""
        lines = ["| 列1 | 列2 |", "| --- | --- |", "---", "| 数据 | 数据 |"]
        fixed, issues = _fix_split_table_separators(lines)
        assert len(fixed) == 3
        assert any("已合并" in i for i in issues)

    def test_merges_truncated_separator(self):
        """分隔行被截断成两行时合并。"""
        lines = ["| 列1 | 列2 |", "| --- | --- |", "--- | --- |", "| 数据 | 数据 |"]
        fixed, issues = _fix_split_table_separators(lines)
        assert len(fixed) == 3
        assert any("截断的分隔行已合并" in i for i in issues)

    def test_removes_blank_line_after_separator(self):
        """分隔行与数据行之间的空行被删除。"""
        lines = ["| 列1 | 列2 |", "| --- | --- |", "", "| 数据 | 数据 |"]
        fixed, issues = _fix_split_table_separators(lines)
        assert fixed == ["| 列1 | 列2 |", "| --- | --- |", "| 数据 | 数据 |"]
        assert any("空行已删除" in i for i in issues)

    def test_normal_table_unchanged(self):
        """正常多行表格不变。"""
        lines = ["| 列1 | 列2 |", "| --- | --- |", "| 数据1 | 数据2 |"]
        fixed, issues = _fix_split_table_separators(lines)
        assert fixed == lines
        assert issues == []


class TestCheckWikilinks:
    """_check_wikilinks 测试。"""

    def test_balanced_links(self):
        """配对的双向链接无问题。"""
        lines = ["- [[概念A]]", "- [[概念B]]"]
        issues = _check_wikilinks(lines)
        assert len(issues) == 0

    def test_unclosed_link(self):
        """未闭合的 [[ 报告问题。"""
        lines = ["- [[概念A"]
        issues = _check_wikilinks(lines)
        assert any("未闭合" in i for i in issues)

    def test_extra_closing(self):
        """多余的 ]] 报告问题。"""
        lines = ["- 概念A]]"]
        issues = _check_wikilinks(lines)
        assert any("多余" in i for i in issues)


class TestCompressBlankLines:
    """_compress_blank_lines 测试。"""

    def test_compresses_excessive_blank_lines(self):
        """压缩过多连续空行。"""
        content = "行1\n\n\n\n\n行2"
        compressed, issue = _compress_blank_lines(content)
        assert compressed.count("\n\n\n") <= 1
        assert issue is not None

    def test_normal_spacing_unchanged(self):
        """正常空行不变。"""
        content = "行1\n\n行2\n\n行3"
        result, issue = _compress_blank_lines(content)
        assert result == content
        assert issue is None
