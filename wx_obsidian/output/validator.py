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

    lines, norm_issue = _normalize_fullwidth_chars(lines)
    if norm_issue:
        issues.append(norm_issue)

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

    lines, split_sep_issues = _fix_split_table_separators(lines)
    issues.extend(split_sep_issues)

    lines, table_issues = _check_tables(lines)
    issues.extend(table_issues)

    content = "\n".join(lines)
    content, space_issue = _compress_blank_lines(content)
    if space_issue:
        issues.append(space_issue)

    content, backslash_issue = _fix_standalone_backslashes(content)
    if backslash_issue:
        issues.append(backslash_issue)

    content = content.rstrip() + "\n"

    return content, issues


# ---------------------------------------------------------------------------
# 内部校验函数
# ---------------------------------------------------------------------------


def _normalize_fullwidth_chars(lines: list[str]) -> tuple[list[str], str | None]:
    """将全角管道符 ＊｜＊ 替换为半角 |（LLM 中文输出常见问题）。"""
    changed = False
    result: list[str] = []
    for line in lines:
        if "｜" in line:
            line = line.replace("｜", "|")
            changed = True
        result.append(line)
    return result, "全角管道符 ｜ 已替换为半角 |" if changed else None


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
    """检查并修复未闭合的代码块，处理孤立的闭合 ```。"""
    issues: list[str] = []

    fence_count = sum(1 for line in lines if line.strip().startswith("```"))
    if fence_count == 0:
        return lines, issues

    if fence_count % 2 == 0:
        # 偶数个 ``` → 配对扫描确认是否正常
        in_block = False
        for line in lines:
            if line.strip().startswith("```"):
                in_block = not in_block
        if in_block:
            # 偶数但未闭合（理论上不会发生，防御性处理）
            issues.append("代码块未闭合，自动补全")
            lines.append("```")
        return lines, issues

    # 奇数个 ``` → 必定有一个孤立
    # 判断是"开启未闭合"还是"闭合无开启"：
    # 检查第一个 ``` 之前是否有内容行（JSON/代码等），若有则是孤立闭合
    first_fence = next(i for i, line in enumerate(lines) if line.strip().startswith("```"))
    has_content_before = any(lines[j].strip() for j in range(first_fence))

    if has_content_before:
        # 孤立的闭合 ``` → 在其前面的非空行前插入 ```
        insert_at = first_fence
        for j in range(first_fence - 1, -1, -1):
            if lines[j].strip():
                insert_at = j
                break
        lines.insert(insert_at, "```")
        issues.append(f"第 {insert_at + 1} 行: 补充缺失的代码块开始标记 ```")
    else:
        # 开启未闭合 → 在末尾补全
        issues.append(f"第 {first_fence + 1} 行: 代码块未闭合，自动补全")
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


def _fix_split_table_separators(lines: list[str]) -> tuple[list[str], list[str]]:
    """修复分隔行与下一行断裂的表格，以及分隔行后的空行。

    处理两种模式：
    1. `| --- |` 后紧跟空行 + `---` → 合并为完整分隔行
    2. `| --- |` 后紧跟 `--- | --- |`（分隔行被截断为两行）→ 合并并删除中间空行
    """
    issues: list[str] = []
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 检测分隔行（完整或不完整）
        if stripped.endswith("|") and re.match(r"^\|[\s]*---[\s]*(\|[\s]*---[\s]*)*\|$", stripped):
            # 向前看：跳过空行
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_stripped = lines[j].strip()
                if next_stripped == "---":
                    # 模式 1：分隔行 + 空行 + --- → 合并
                    result.append(stripped.rstrip() + " --- |")
                    issues.append(f"第 {i + 1} 行: 分隔行与 --- 已合并")
                    i = j + 1
                    continue
                if re.match(r"^---+\s*\|", next_stripped) and "|" in next_stripped:
                    # 模式 2：分隔行被截断，续行以 ---| 开头
                    # 合并为一行：去掉续行开头多余的 -
                    continuation = re.sub(r"^---+", " --- ", next_stripped).strip()
                    merged = stripped.rstrip() + continuation
                    if not merged.endswith("|"):
                        merged += " |"
                    result.append(merged)
                    issues.append(f"第 {i + 1}-{j + 1} 行: 截断的分隔行已合并")
                    # 跳过续行及其后的空行
                    i = j + 1
                    while i < len(lines) and not lines[i].strip():
                        i += 1
                    continue
            # 分隔行已完整，但后面有空行隔断数据行 → 删除空行
            if j > i + 1:
                result.append(line)
                issues.append(f"第 {i + 2}-{j} 行: 分隔行后空行已删除")
                i = j
                continue
        result.append(line)
        i += 1
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


def _fix_standalone_backslashes(content: str) -> tuple[str, str | None]:
    """修复单独的反斜杠行（LLM 生成的无效字符）。"""
    # 匹配只包含反斜杠和空白的行
    pattern = re.compile(r"^\s*\\\s*$", re.MULTILINE)
    if pattern.search(content):
        fixed = pattern.sub("", content)
        # 清理因此产生的多余空行
        fixed = re.sub(r"\n{3,}", "\n\n", fixed)
        return fixed, "移除了单独的反斜杠行"
    return content, None


# ---------------------------------------------------------------------------
# 基于 mistune 的结构性校验
# ---------------------------------------------------------------------------


def detect_format_issues(content: str) -> list[str]:
    """用 mistune 解析 markdown，检测自动修复无法处理的结构性问题。

    与 validate_and_fix 互补：validate_and_fix 做自动修复，
    detect_format_issues 发现需要 LLM 重新生成的问题。
    """
    try:
        import mistune
    except ImportError:
        return []

    issues: list[str] = []
    md_parser = mistune.create_markdown(plugins=["table"])
    html = str(md_parser(content))

    # 表格：统计 markdown 中的表格块数 vs HTML 中的 <table> 数
    lines = content.split("\n")
    table_block_count = 0
    in_table = False
    for line in lines:
        is_table_row = line.strip().startswith("|") and len(line.strip()) > 3
        if is_table_row and not in_table:
            table_block_count += 1
            in_table = True
        elif not is_table_row and in_table:
            in_table = False
    html_table_count = html.count("<table>")
    if table_block_count > 0 and html_table_count < table_block_count:
        issues.append(
            f"表格格式错误：markdown 中有 {table_block_count} 个表格块，"
            f"但只有 {html_table_count} 个渲染为有效表格。"
            "常见原因：分隔行（| --- |）列数与表头不一致、分隔行被截断、"
            "表格行之间有空行打断。请确保分隔行列数与表头完全匹配，"
            "表格内不要有空行。"
        )

    # 代码块未闭合
    code_opens = content.count("```")
    if code_opens % 2 != 0:
        issues.append(f"代码块未闭合（检测到 {code_opens} 个 ``` 标记）")

    return issues


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
        if len(sys.argv) < 3:
            print("错误: --dir 需要指定目录路径")
            sys.exit(1)
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
