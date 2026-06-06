"""文章级联删除：清理文章文件、MOC 条目、概念页面、日归档和 processed.json 记录。"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from wx_obsidian.config import save_processed

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, content: str) -> None:
    """原子写入文件：先写临时文件，再 os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def find_article(processed: dict[str, Any], query: str) -> str | None:
    """在 processed.json 中查找文章，返回 article_id。

    匹配优先级：
    1. article_id 精确匹配
    2. title 精确匹配
    3. title 子串匹配（大小写不敏感）

    Returns:
        article_id（精确匹配或唯一子串匹配），
        ""（多条子串匹配，候选列表已打印），
        None（无匹配）。
    """
    # 1. article_id 精确匹配
    if query in processed and isinstance(processed[query], dict):
        return query

    # 2. title 精确匹配
    for aid, record in processed.items():
        if isinstance(record, dict) and record.get("title") == query:
            return aid

    # 3. title 子串匹配
    query_lower = query.lower()
    matches: list[tuple[str, str]] = []
    for aid, record in processed.items():
        if isinstance(record, dict) and query_lower in record.get("title", "").lower():
            matches.append((aid, record.get("title", "")))

    if len(matches) == 1:
        return matches[0][0]
    if len(matches) > 1:
        print(f"匹配到 {len(matches)} 篇文章，请用更精确的标题重试：")
        for i, (aid, title) in enumerate(matches, 1):
            print(f"  [{i}] {title}  (ID: {aid})")
        return ""

    return None


def cascade_delete(
    vault_path: Path,
    articles_dir: Path,
    processed: dict[str, Any],
    article_id: str,
) -> list[str]:
    """级联删除文章及其所有关联数据。

    每步操作独立 try/except，单步失败不影响后续步骤。
    processed.json 记录移除放在最后，确保即使中途失败也能重试。

    Returns:
        已清理的项目列表（用于打印）。
    """
    record = processed.get(article_id)
    if not isinstance(record, dict):
        return [f"未找到文章记录: {article_id}"]

    actions: list[str] = []
    errors: list[str] = []
    title = record.get("title", "未知")
    category = record.get("category", "")
    safe_title = Path(record.get("file", "")).stem or re.sub(r'[<>:"/\\|?*]', "_", title)[:100]
    date = record.get("date", "")
    concepts: list[dict[str, str]] = record.get("concepts") or []

    # 1. 删除文章文件
    try:
        file_path = Path(record.get("file", ""))
        if file_path.exists():
            file_path.unlink()
            actions.append(f"已删除文件: {file_path}")
        else:
            actions.append(f"文件不存在（跳过）: {file_path}")
    except OSError as e:
        errors.append(f"删除文件失败: {e}")
        logger.warning("删除文件失败: %s", e)

    # 2. 移除 MOC 条目
    if category:
        try:
            moc_actions = _remove_moc_entry(articles_dir, category, safe_title, date)
            actions.extend(moc_actions)
        except OSError as e:
            errors.append(f"移除 MOC 条目失败: {e}")
            logger.warning("移除 MOC 条目失败: %s", e)

    # 3. 清理概念页面
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        concept_name = concept.get("name", "")
        if concept_name:
            try:
                concept_actions = _cleanup_concept_page(
                    articles_dir, concept_name, safe_title, title, category
                )
                actions.extend(concept_actions)
            except OSError as e:
                errors.append(f"清理概念页面 {concept_name} 失败: {e}")
                logger.warning("清理概念页面失败 [%s]: %s", concept_name, e)

    # 4. 清理日归档
    if date:
        try:
            archive_actions = _remove_archive_entry(articles_dir, date, safe_title, category)
            actions.extend(archive_actions)
        except OSError as e:
            errors.append(f"清理日归档失败: {e}")
            logger.warning("清理日归档失败: %s", e)

    # 5. 从 processed.json 移除记录（最后执行，确保前面失败时可重试）
    try:
        processed.pop(article_id, None)
        save_processed(processed)
        actions.append("已从 processed.json 移除记录")
    except OSError as e:
        errors.append(f"更新 processed.json 失败: {e}")
        logger.warning("更新 processed.json 失败: %s", e)

    if errors:
        actions.append(f"⚠ {len(errors)} 项操作失败，可重新执行以完成清理")

    return actions


def _remove_moc_entry(articles_dir: Path, category: str, safe_title: str, date: str) -> list[str]:
    """从分类 _MOC.md 移除文章条目。"""
    moc_file = articles_dir / category / "_MOC.md"
    if not moc_file.exists():
        return []

    content = moc_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    # 匹配格式: "- DATE [[TITLE]]" 或 "- DATE [[CATEGORY/TITLE|ALIAS]]"
    new_lines = [
        line for line in lines if safe_title not in line or not line.strip().startswith("-")
    ]

    if len(new_lines) == len(lines):
        return [f"MOC 条目未找到: {safe_title}"]

    _atomic_write(moc_file, "\n".join(new_lines))
    return [f"已从 {category}/_MOC.md 移除条目"]


def _cleanup_concept_page(
    articles_dir: Path,
    concept_name: str,
    safe_title: str,
    article_title: str,
    category: str,
) -> list[str]:
    """清理概念页面中的文章链接，无剩余链接时删除页面。"""
    concept_file = articles_dir / "概念" / f"{concept_name}.md"
    if not concept_file.exists():
        return []

    content = concept_file.read_text(encoding="utf-8")
    actions: list[str] = []

    # 移除该文章的 wikilink 行
    article_ref = re.sub(r'[<>:"/\\|?*]', "_", article_title)[:100]
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        # 匹配 wikilink 行：- [[...safe_title...]] 或 - [[category/safe_title|...]]
        if line.strip().startswith("- [[") and safe_title in line:
            continue
        # 也匹配用 article_title 构建的引用
        if line.strip().startswith("- [[") and article_ref in line:
            continue
        new_lines.append(line)

    if len(new_lines) == len(lines):
        return []

    new_content = "\n".join(new_lines)

    # 检查 "## 相关文章" 部分是否还有实际链接
    if "## 相关文章" in new_content:
        after_section = new_content.split("## 相关文章", 1)[1]
        # 去掉占位行 "> 自动更新"，看是否还有链接
        remaining = re.sub(r">\s*自动更新\s*", "", after_section).strip()
        has_links = bool(re.search(r"- \[\[", remaining))

        if not has_links:
            # 没有其他文章引用，删除整个概念页面
            concept_file.unlink()
            actions.append(f"已删除概念页面（无其他引用）: 概念/{concept_name}.md")
            return actions

    # 还有其他链接，写回
    _atomic_write(concept_file, new_content)
    actions.append(f"已从概念/{concept_name}.md 移除文章链接")
    return actions


def _remove_archive_entry(
    articles_dir: Path, date_str: str, safe_title: str, category: str
) -> list[str]:
    """从日归档文件移除文章条目。"""
    parts = date_str.split("-")
    if len(parts) != 3:
        return []

    yy, mm, dd = parts[0][2:], parts[1], parts[2]
    archive_file = articles_dir / "Z归档" / yy / mm / f"{dd}.md"
    if not archive_file.exists():
        return []

    content = archive_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines: list[str] = []
    skip_next_indent = False

    for line in lines:
        if safe_title in line and line.strip().startswith("- "):
            # 这是文章条目行，跳过
            skip_next_indent = True
            continue
        if skip_next_indent and line.startswith("  "):
            # 这是文章摘要的缩进行，跳过
            continue
        skip_next_indent = False
        new_lines.append(line)

    if len(new_lines) == len(lines):
        return []

    # 清理空的分类标题（## category 后面没有条目了）
    final_lines: list[str] = []
    for i, line in enumerate(new_lines):
        if line.startswith("## "):
            # 检查下一个非空行是否也是 ## 或文件结尾
            has_entries = False
            for j in range(i + 1, len(new_lines)):
                if new_lines[j].startswith("## ") or new_lines[j].startswith("# "):
                    break
                if new_lines[j].strip().startswith("- "):
                    has_entries = True
                    break
            if has_entries:
                final_lines.append(line)
        else:
            final_lines.append(line)

    _atomic_write(archive_file, "\n".join(final_lines))
    return [f"已从 Z归档/{yy}/{mm}/{dd}.md 移除条目"]
