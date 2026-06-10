"""文章级联删除：清理文章文件、MOC 条目、概念页面、日归档和 processed.json 记录。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from wx_obsidian.config import atomic_write, save_processed

logger = logging.getLogger(__name__)


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

    # 2. 移除 MOC 条目（MOC 中存储的是 safe_title，即文件名）
    if category:
        try:
            # 优先从文件实际路径推断 MOC 所在目录（处理子目录迁移的情况）
            file_path = Path(record.get("file", ""))
            moc_dir = _resolve_moc_dir(articles_dir, category, file_path)
            moc_actions = _remove_moc_entry(moc_dir, safe_title)
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
                    articles_dir, concept_name, title, safe_title, category
                )
                actions.extend(concept_actions)
            except OSError as e:
                errors.append(f"清理概念页面 {concept_name} 失败: {e}")
                logger.warning("清理概念页面失败 [%s]: %s", concept_name, e)

    # 3.5 清理孤立概念页面（"相关文章"部分无任何文章链接的概念页面）
    try:
        orphan_actions = _cleanup_orphaned_concepts(articles_dir)
        actions.extend(orphan_actions)
    except OSError as e:
        errors.append(f"清理孤立概念页面失败: {e}")
        logger.warning("清理孤立概念页面失败: %s", e)

    # 4. 清理日归档（归档文件用 safe_title，因为写入时就是用 safe_title 构建的）
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


def _resolve_moc_dir(articles_dir: Path, category: str, file_path: Path) -> Path:
    """从文件实际路径推断 MOC 所在目录。

    文章可能被迁移到子目录（如 category/sub_topic/），此时 MOC 在子目录中。
    如果文件路径的父目录是 category 的子目录，返回子目录；否则返回 category 目录。
    """
    if not file_path.exists():
        return articles_dir / category
    parent = file_path.parent
    category_dir = articles_dir / category
    # 文件直接在 category 目录下
    if parent == category_dir:
        return category_dir
    # 文件在 category 的子目录下（子目录迁移）
    if category_dir in parent.parents:
        return parent
    return category_dir


def _matches_wikilink(line: str, title: str) -> bool:
    """检查行中的 wikilink 是否引用了 title（按路径段匹配，非子串）。"""
    if not line.strip().startswith("- ") or "[[" not in line:
        return False
    m = re.search(re.escape(title), line)
    if not m:
        return False
    # 确保匹配的是 wikilink 中的路径段（前面是 [[ 或 /，后面是 | 或 ]]）
    before_ok = m.start() == 0 or line[m.start() - 1] in "[/"  # noqa: PLC1901
    after_pos = m.end()
    after_ok = after_pos >= len(line) or line[after_pos] in "|]/"  # noqa: PLC1901
    return before_ok and after_ok


def _remove_moc_entry(moc_dir: Path, safe_title: str) -> list[str]:
    """从分类 _MOC.md 移除文章条目。"""
    moc_file = moc_dir / "_MOC.md"
    if not moc_file.exists():
        return []

    content = moc_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    new_lines = [line for line in lines if not _matches_wikilink(line, safe_title)]

    if len(new_lines) == len(lines):
        return [f"MOC 条目未找到: {safe_title}"]

    atomic_write(moc_file, "\n".join(new_lines))
    return [f"已从 {moc_dir.name}/_MOC.md 移除条目"]


def _cleanup_concept_page(
    articles_dir: Path,
    concept_name: str,
    article_title: str,
    safe_title: str,
    category: str,
) -> list[str]:
    """清理概念页面中的文章链接，无剩余链接时删除页面。"""
    concept_file = articles_dir / "概念" / f"{concept_name}.md"
    if not concept_file.exists():
        return []

    content = concept_file.read_text(encoding="utf-8")
    actions: list[str] = []

    # 移除该文章的 wikilink 行（同时匹配原始标题和 safe_title）
    lines = content.split("\n")
    new_lines = []
    for line in lines:
        if line.strip().startswith("- [[") and (article_title in line or safe_title in line):
            continue
        new_lines.append(line)

    content_changed = len(new_lines) != len(lines)
    new_content = "\n".join(new_lines)

    # 检查概念页面是否仍引用该文章（标题可能出现在描述中）
    references_article = article_title in new_content or safe_title in new_content

    if not references_article:
        if content_changed:
            atomic_write(concept_file, new_content)
            actions.append(f"已从概念/{concept_name}.md 移除文章链接")
        return actions

    # 概念页面仍引用该文章，检查是否有其他文章链接
    has_other_links = False
    if "## 相关文章" in new_content:
        after_section = new_content.split("## 相关文章", 1)[1]
        remaining = re.sub(r">\s*自动更新\s*", "", after_section).strip()
        has_other_links = bool(re.search(r"- \[\[", remaining))

    if not has_other_links:
        concept_file.unlink()
        actions.append(f"已删除概念页面（仅关联已删除文章）: 概念/{concept_name}.md")
        return actions

    if content_changed:
        atomic_write(concept_file, new_content)
        actions.append(f"已从概念/{concept_name}.md 移除文章链接")
    return actions


def _cleanup_orphaned_concepts(articles_dir: Path) -> list[str]:
    """清理"相关文章"部分无任何文章链接的孤立概念页面。"""
    concept_dir = articles_dir / "概念"
    if not concept_dir.exists():
        return []

    actions: list[str] = []
    for concept_file in concept_dir.glob("*.md"):
        if concept_file.name == "_MOC.md":
            continue
        content = concept_file.read_text(encoding="utf-8")
        if "## 相关文章" not in content:
            continue

        after_section = content.split("## 相关文章", 1)[1]
        remaining = re.sub(r">\s*自动更新\s*", "", after_section).strip()
        has_links = bool(re.search(r"- \[\[", remaining))

        if not has_links:
            concept_file.unlink()
            actions.append(f"已删除孤立概念页面: 概念/{concept_file.name}")

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
        if _matches_wikilink(line, safe_title):
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

    atomic_write(archive_file, "\n".join(final_lines))
    return [f"已从 Z归档/{yy}/{mm}/{dd}.md 移除条目"]
