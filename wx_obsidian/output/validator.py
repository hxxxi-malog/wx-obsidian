#!/usr/bin/env python3
"""Markdown 文档格式校验与自动修复工具。

对 Obsidian 笔记执行结构化校验，自动修复常见格式问题，
包括 frontmatter 缺失、代码块未闭合、表格语法错误等。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# frontmatter 必需字段
_ARTICLE_REQUIRED_FIELDS = ("title", "source", "date", "tags", "category")
_CONCEPT_REQUIRED_FIELDS = ("tags",)


def validate_and_fix(content: str, *, is_concept: bool = False) -> tuple[str, list[str]]:
    """校验并修复 Markdown 内容。

    Args:
        content: Markdown 原始文本。
        is_concept: 是否为概念页面（结构更简单，允许 # 标题和简化 frontmatter）。

    Returns:
        (修复后内容, 问题列表)。
    """
    issues: list[str] = []
    lines = content.split("\n")

    lines, fm_issues = _check_frontmatter(lines, is_concept)
    issues.extend(fm_issues)

    if not is_concept:
        lines, title_issue = _remove_duplicate_title(lines)
        if title_issue:
            issues.append(title_issue)

    lines, code_issues = _check_unclosed_code_block(lines)
    issues.extend(code_issues)

    link_issues = _check_wikilinks(lines)
    issues.extend(link_issues)

    lines, compress_issues = _fix_compressed_tables(lines)
    issues.extend(compress_issues)

    lines, table_issues = _check_tables(lines)
    issues.extend(table_issues)

    content = "\n".join(lines)
    content, space_issue = _compress_blank_lines(content)
    if space_issue:
        issues.append(space_issue)

    content = content.rstrip() + "\n"

    if not is_concept:
        footer_issue = _check_footer(content)
        if footer_issue:
            issues.append(footer_issue)

    return content, issues


# ---------------------------------------------------------------------------
# 内部校验函数
# ---------------------------------------------------------------------------


def _check_frontmatter(lines: list[str], is_concept: bool) -> tuple[list[str], list[str]]:
    """校验 YAML frontmatter，返回 (lines, issues)。"""
    issues: list[str] = []

    if not lines or lines[0].strip() != "---":
        issues.append("缺少 frontmatter 起始标记 ---")
        return lines, issues

    fm_end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break

    if fm_end == -1:
        issues.append("frontmatter 缺少结束标记 ---")
        return lines, issues

    fm_block = "\n".join(lines[1:fm_end])
    required = _CONCEPT_REQUIRED_FIELDS if is_concept else _ARTICLE_REQUIRED_FIELDS

    for field in required:
        if f"{field}:" not in fm_block:
            issues.append(f"frontmatter 缺少字段: {field}")

    for i, line in enumerate(lines[1:fm_end], start=2):
        if '"' in line and line.count('"') % 2 != 0:
            parts = line.split(":", 1)
            if len(parts) == 2:
                val = parts[1].strip()
                if val.startswith('"') and not val.endswith('"'):
                    issues.append(f"第 {i} 行: frontmatter 引号未闭合: {line.strip()}")

    return lines, issues


def _remove_duplicate_title(lines: list[str]) -> tuple[list[str], str | None]:
    """删除正文中与 frontmatter 重复的顶级标题。"""
    for line in lines:
        if line.strip() == "---":
            continue
        if line.startswith("# ") and not line.startswith("## "):
            filtered = [
                ln for ln in lines if not (ln.startswith("# ") and not ln.startswith("## "))
            ]
            return filtered, f"正文不应有顶级标题（已在 frontmatter 中）: {line[:40]}"
    return lines, None


def _check_unclosed_code_block(lines: list[str]) -> tuple[list[str], list[str]]:
    """检查并修复未闭合的代码块。"""
    issues: list[str] = []
    in_code_block = False
    code_block_start = -1

    for i, line in enumerate(lines):
        if line.strip().startswith("```"):
            if not in_code_block:
                in_code_block = True
                code_block_start = i
            else:
                in_code_block = False

    if in_code_block:
        issues.append(f"第 {code_block_start + 1} 行: 代码块未闭合，自动补全")
        lines.append("```")

    return lines, issues


def _check_wikilinks(lines: list[str]) -> list[str]:
    """检查双向链接是否配对。"""
    issues: list[str] = []
    for i, line in enumerate(lines, start=1):
        opens = line.count("[[")
        closes = line.count("]]")
        if opens > closes:
            issues.append(f"第 {i} 行: 未闭合的双向链接 [[")
        elif closes > opens:
            issues.append(f"第 {i} 行: 多余的 ]]")
    return issues


def _fix_compressed_tables(lines: list[str]) -> tuple[list[str], list[str]]:
    """修复被压缩到单行的表格（LLM JSON 响应中换行符被转义）。

    检测模式：一行中包含 '| --- |' 分隔符，说明多行表格被挤到了一行。
    """
    issues: list[str] = []
    result: list[str] = []

    for i, line in enumerate(lines):
        # 检测压缩表格：一行中有分隔符 '| --- |'
        sep_match = re.search(r"(\|[\s]*---[\s]*(?:\|[\s]*---[\s]*)*\|)", line)
        if not sep_match:
            result.append(line)
            continue

        sep = sep_match.group(1)
        before = line[: sep_match.start()].strip()
        after = line[sep_match.end() :].strip()

        # 确保 header 行以 | 结尾
        if before and not before.endswith("|"):
            before += " |"

        # 按行边界分割 data rows：| 后跟空格再跟非 - 内容
        data_rows = [r.strip() for r in re.split(r"(?<=\|)\s+(?=\|[^-])", after) if r.strip()]

        if before:
            result.append(before)
        result.append(sep)
        result.extend(data_rows)
        issues.append(f"第 {i + 1} 行: 压缩表格已拆分为 {2 + len(data_rows)} 行")

    return result, issues


def _check_tables(lines: list[str]) -> tuple[list[str], list[str]]:
    """检查并修复表格格式问题。"""
    issues: list[str] = []

    # 修复表格前缺少空行的问题（Obsidian 要求表格前有空行才能正确渲染）
    insertions: list[int] = []
    for i, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        # 当前行是表格行，检查前一行是否非空且非表格行
        if i > 0 and lines[i - 1].strip() and not lines[i - 1].strip().startswith("|"):
            insertions.append(i)
    for idx in reversed(insertions):
        lines.insert(idx, "")
        issues.append(f"第 {idx + 1} 行: 表格前缺少空行，已自动插入")

    for i, line in enumerate(lines):
        if "|" not in line or not line.strip().startswith("|"):
            continue

        stripped = line.strip()

        # 修复行末多余的反斜杠（DeepSeek 的 \| 转义残留）
        if re.search(r"\s*\\$", stripped) and not stripped.endswith("|"):
            fixed = re.sub(r"\s*\\$", "", stripped)
            if not fixed.endswith("|"):
                fixed += " |"
            lines[i] = fixed
            issues.append(f"第 {i + 1} 行: 表格行末多余反斜杠，已修复")
            stripped = fixed

        # 修复 "| xxx \ |" → "| xxx |"
        if re.search(r"\\\s*\|", stripped):
            fixed = re.sub(r"\s*\\\s*\|", " |", stripped)
            lines[i] = fixed
            issues.append(f"第 {i + 1} 行: 表格内多余反斜杠，已修复")
            stripped = fixed

        # 表格行应以 | 结尾
        if not stripped.endswith("|"):
            issues.append(f"第 {i + 1} 行: 表格行未闭合，自动修复")
            lines[i] = stripped + " |"

    return lines, issues


def _compress_blank_lines(content: str) -> tuple[str, str | None]:
    """压缩连续空行（最多保留 2 行空行）。"""
    compressed = re.sub(r"\n{4,}", "\n\n\n", content)
    if compressed != content:
        return compressed, "压缩了过多连续空行"
    return content, None


def _check_footer(content: str) -> str | None:
    """检查是否存在来源 footer。"""
    if "> 来源：" not in content:
        return "缺少来源 footer（> 来源：xxx | [原文链接](url)）"
    return None


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def validate_file(filepath: str | Path) -> bool:
    """校验单个文件，返回是否通过。"""
    path = Path(filepath)
    if not path.exists():
        print(f"  文件不存在: {filepath}")
        return False

    is_concept = "概念" in str(path)
    content = path.read_text(encoding="utf-8")
    fixed, issues = validate_and_fix(content, is_concept=is_concept)

    if issues:
        print(f"  发现 {len(issues)} 个问题:")
        for issue in issues:
            print(f"    - {issue}")
        if fixed != content:
            path.write_text(fixed, encoding="utf-8")
            print("  已自动修复并写回")
        return False

    print("  格式校验通过")
    return True


def main() -> None:
    """CLI 入口，支持单文件和目录扫描。"""
    if len(sys.argv) < 2:
        print("用法: python validate_markdown.py <file.md> [file2.md ...]")
        print("      python validate_markdown.py --dir <目录>")
        sys.exit(1)

    if sys.argv[1] == "--dir":
        _scan_directory(Path(sys.argv[2]))
    else:
        for filepath in sys.argv[1:]:
            print(f"\n{filepath}:")
            validate_file(filepath)


def _scan_directory(directory: Path) -> None:
    """扫描目录下所有 .md 文件并校验。"""
    md_files = sorted(directory.rglob("*.md"))
    print(f"扫描 {directory} 下 {len(md_files)} 个文件...")
    passed = failed = 0

    for f in md_files:
        if f.name == "_MOC.md":
            continue
        print(f"\n{f.relative_to(directory)}:")
        if validate_file(f):
            passed += 1
        else:
            failed += 1

    print(f"\n总结: {passed} 通过, {failed} 有问题")


if __name__ == "__main__":
    main()
