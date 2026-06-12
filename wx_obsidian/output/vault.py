"""Obsidian Vault 操作：MOC 更新、概念页面、分类管理、子目录拆分。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wx_obsidian.config import (
    SUB_TOPIC_THRESHOLD,
    atomic_write,
    sanitize_path_segment,
    save_processed,
)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def escape_display(text: str) -> str:
    """清理显示文本中会破坏 wikilink 语法的字符。"""
    return re.sub(r"[\[\]]", "", text)


def normalize_quotes(text: str) -> str:
    """将弯引号统一为直引号，避免文件名与链接目标因引号类型不同而失配。

    Obsidian 在解析 wikilink 时对引号类型敏感：文件名中的 “” (U+201C/U+201D)
    与链接中的 "" (U+0022) 不会互相匹配。统一为直引号可防止此类断链。
    """
    return text.replace("“", '"').replace("”", '"')


# ---------------------------------------------------------------------------
# 概念页面
# ---------------------------------------------------------------------------


def ensure_concept_page(
    vault_path: Path,
    concept_name: str,
    description: str,
    articles_dir: Path | None = None,
    article_title: str = "",
    article_category: str = "",
) -> None:
    """确保概念页面存在，不存在则创建；已存在则追加相关文章链接。"""
    # 防御性清理：去除 LLM 可能添加的 [[...]] wiki-link 语法
    concept_name = escape_display(concept_name).strip()

    base = articles_dir or (vault_path / "公众号文章")
    concept_dir = base / "概念"
    concept_file = concept_dir / f"{concept_name}.md"

    if not concept_file.exists():
        concept_dir.mkdir(parents=True, exist_ok=True)
        content = f"""---
tags: [概念]
---

# {concept_name}

{description}

## 相关文章
> 自动更新
"""
        atomic_write(concept_file, content)

    if article_title:
        _append_related_article(concept_file, article_title, article_category)


def _append_related_article(concept_file: Path, article_title: str, article_category: str) -> None:
    """向概念页面的"相关文章"部分追加文章链接。

    按文章标题（显示文本）去重：若已存在同标题链接但 category 路径不同
    （例如文章被 maybe_create_subcategory 移入子目录后），自动更新链接路径。
    """
    content = concept_file.read_text(encoding="utf-8")
    safe_title = sanitize_path_segment(article_title)
    if article_category:
        display = escape_display(article_title)
        wikilink = f"- [[{article_category}/{safe_title}|{display}]]"
    else:
        wikilink = f"- [[{safe_title}]]"

    # 按文章标题（显示文本）检查是否已存在
    escaped_title = re.escape(article_title)
    existing_pattern = re.compile(r"- \[\[[^\]]*?" + escaped_title + r"\]\]")
    existing_match = existing_pattern.search(content)

    if existing_match:
        existing_link = existing_match.group(0)
        if existing_link == wikilink:
            return  # 链接完全一致，无需操作
        # category 路径已变更（文章被移入子目录），更新链接
        content = content[: existing_match.start()] + wikilink + content[existing_match.end() :]
        atomic_write(concept_file, content)
        return

    if "## 相关文章" in content:
        parts = content.split("## 相关文章", 1)
        after = parts[1]
        # 在"## 相关文章"标题后追加（跳过"> 自动更新"占位行）
        after = re.sub(r"(> 自动更新\n?)", r"\1" + wikilink + "\n", after, count=1)
        if wikilink not in after:
            # fallback：直接在标题后追加
            after = after.lstrip("\n") + wikilink + "\n"
        content = parts[0] + "## 相关文章" + after
    else:
        content = content.rstrip() + f"\n\n## 相关文章\n{wikilink}\n"

    atomic_write(concept_file, content)


# ---------------------------------------------------------------------------
# MOC 更新
# ---------------------------------------------------------------------------


def _parse_moc_content(content: str) -> tuple[str, list[tuple[str, list[str]]], list[str]]:
    """解析 MOC 文件内容为结构化数据。

    Returns:
        (title, folder_groups, standalone_entries)
        - title: MOC 标题行（如 "# Agent"）
        - folder_groups: [(folder_line, [article_line, ...]), ...]
        - standalone_entries: [article_line, ...]（不属于任何文件夹的文章）
    """
    lines = content.split("\n")
    title = ""
    folder_groups: list[tuple[str, list[str]]] = []
    standalone: list[str] = []

    current_folder: str | None = None
    current_articles: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped
            continue
        if not stripped:
            continue

        if stripped.startswith("- 📁 "):
            # 新文件夹条目，先保存上一个文件夹
            if current_folder is not None:
                folder_groups.append((current_folder, current_articles))
            current_folder = stripped
            current_articles = []
        elif stripped.startswith("- ") and current_folder is not None:
            current_articles.append(stripped)
        elif stripped.startswith("- "):
            standalone.append(stripped)

    # 保存最后一个文件夹
    if current_folder is not None:
        folder_groups.append((current_folder, current_articles))

    return title or "# (untitled)", folder_groups, standalone


def _insert_into_folder_group(
    folder_groups: list[tuple[str, list[str]]],
    folder_name: str,
    entry: str,
) -> bool:
    """在文件夹组中按日期顺序插入条目。

    注意：此函数原地修改 folder_groups 列表（mutation），调用方需感知。

    Args:
        folder_groups: _parse_moc_content 返回的文件夹组。
        folder_name: 目标文件夹名（如 "多Agent协同"）。
        entry: 要插入的条目行。

    Returns:
        True 表示插入成功，False 表示未找到匹配的文件夹。
    """
    for i, (folder_line, articles) in enumerate(folder_groups):
        if f"/{folder_name}/" in folder_line or f"|{folder_name}]]" in folder_line:
            # 提取新条目日期
            new_date = ""
            date_match = re.match(r"- (\d{4}-\d{2}-\d{2})\s", entry)
            if date_match:
                new_date = date_match.group(1)

            # 找到插入位置（按日期倒序，最新在前）
            insert_idx = len(articles)
            if new_date:
                for j, existing in enumerate(articles):
                    existing_date = ""
                    em = re.match(r"- (\d{4}-\d{2}-\d{2})\s", existing)
                    if em:
                        existing_date = em.group(1)
                    if existing_date and new_date > existing_date:
                        insert_idx = j
                        break

            articles.insert(insert_idx, entry)
            folder_groups[i] = (folder_line, articles)
            return True
    return False


def _rebuild_moc(
    title: str, folder_groups: list[tuple[str, list[str]]], standalone: list[str]
) -> str:
    """从结构化数据重建 MOC 文件内容。"""
    parts = [title, ""]
    for folder_line, articles in folder_groups:
        parts.append(folder_line)
        for article in articles:
            parts.append(f"  {article}")
    for entry in standalone:
        parts.append(entry)
    return "\n".join(parts) + "\n"


def update_moc(
    vault_path: Path,
    category: str,
    title: str,
    date: str,
    articles_dir: Path | None = None,
    original_title: str = "",
) -> None:
    """更新分类 MOC 文件，追加新文章链接。

    如果 category 包含子目录（如 "Agent/RAG"），同时更新父 MOC
    中对应文件夹组下的条目。

    Args:
        category: 文章分类路径（如 "Agent" 或 "Agent/RAG"）。
        title: safe_title（文件名），用作 wikilink 目标。
        original_title: 原始标题，用作 wikilink 显示文本。为空时直接用 title。
    """
    base = articles_dir or (vault_path / "公众号文章")
    category_dir = base / category
    category_dir.mkdir(parents=True, exist_ok=True)

    moc_file = category_dir / "_MOC.md"
    if not moc_file.exists():
        atomic_write(moc_file, f"# {category}\n\n")

    content = moc_file.read_text(encoding="utf-8")
    # 使用完整路径（category/title）确保链接在子目录迁移后仍然有效
    full_path = f"{category}/{title}"
    display = escape_display(original_title if original_title else title)
    wikilink = f"[[{full_path}|{display}]]"
    entry = f"- {date} {wikilink}"
    if entry not in content:
        content = content.rstrip() + f"\n{entry}"
        atomic_write(moc_file, content)

    # 如果是子目录文章，同时更新父 MOC 的文件夹组
    if "/" in category:
        parent_cat, subfolder = category.rsplit("/", 1)
        parent_moc = base / parent_cat / "_MOC.md"
        if parent_moc.exists():
            parent_content = parent_moc.read_text(encoding="utf-8")
            title_line, folder_groups, standalone = _parse_moc_content(parent_content)
            if entry not in parent_content and _insert_into_folder_group(
                folder_groups, subfolder, entry
            ):
                # 清理 standalone 中同名文章的旧条目（路径已过时）
                # 用正则精确匹配 wikilink 目标中的文件名段，避免子串误伤
                # （如 title='AI' 不应误删 '[[Agent/AI Agent|AI Agent]]'）
                title_pat = re.compile(r"\[\[[^\]]*?/" + re.escape(title) + r"(?:\||\]\])")
                standalone = [e for e in standalone if not title_pat.search(e)]
                new_content = _rebuild_moc(title_line, folder_groups, standalone)
                atomic_write(parent_moc, new_content)


# ---------------------------------------------------------------------------
# 分类管理
# ---------------------------------------------------------------------------


def ensure_category(
    vault_path: Path,
    config: dict[str, Any],
    category: str,
    articles_dir: Path | None = None,
) -> None:
    """确保分类存在：创建目录、MOC 文件，并在内存中追加到 config["categories"]。"""
    if category in config["categories"]:
        return

    print(f"  发现新分类：{category}，自动创建...")

    base = articles_dir or (vault_path / "公众号文章")
    category_dir = base / category
    category_dir.mkdir(parents=True, exist_ok=True)

    moc_file = category_dir / "_MOC.md"
    if not moc_file.exists():
        atomic_write(moc_file, f"# {category}\n\n")

    config["categories"].append(category)

    root_moc = base / "_MOC.md"
    if root_moc.exists():
        content = root_moc.read_text(encoding="utf-8")
        if f"[[{category}/_MOC" not in content:
            folder_entry = f"- 📁 [[{category}/_MOC|{escape_display(category)}]]"
            content = content.rstrip() + f"\n{folder_entry}"
            atomic_write(root_moc, content)


# ---------------------------------------------------------------------------
# 子目录拆分
# ---------------------------------------------------------------------------


def _count_sub_topic_articles(processed: dict[str, Any], category: str, sub_topic: str) -> int:
    """统计同一分类下同一子主题的文章数量。"""
    return sum(
        1
        for record in processed.values()
        if isinstance(record, dict)
        and record.get("status") == "done"
        and record.get("category") == category
        and record.get("sub_topic") == sub_topic
    )


def maybe_create_subcategory(
    vault_path: Path,
    config: dict[str, Any],
    processed: dict[str, Any],
    category: str,
    sub_topic: str,
) -> None:
    """当同一子主题积累足够文章时，创建子目录并迁移文件。"""
    if not sub_topic:
        return

    count = _count_sub_topic_articles(processed, category, sub_topic)
    if count < SUB_TOPIC_THRESHOLD:
        return

    articles_dir = vault_path / config["obsidian"]["articles_dir"]
    sub_dir = articles_dir / category / sub_topic

    if sub_dir.exists():
        return

    print(f"  子主题「{sub_topic}」已有 {count} 篇文章，创建子目录 {category}/{sub_topic}/")
    sub_dir.mkdir(parents=True, exist_ok=True)

    moc_file = sub_dir / "_MOC.md"
    if not moc_file.exists():
        atomic_write(moc_file, f"# {sub_topic}\n\n")

    concept_dir = articles_dir / "概念"
    _migrate_articles_to_subdir(
        processed, category, sub_topic, articles_dir, sub_dir, moc_file, concept_dir
    )
    # 立即持久化，防止后续 _fix_links_batch / _update_parent_moc 失败导致
    # 文件已移走但 processed.json 仍指向旧路径
    save_processed(processed)
    _update_parent_moc(articles_dir, category, sub_topic)


def _fix_concept_links(
    concept_dir: Path,
    article_title: str,
    old_category: str,
    new_category: str,
) -> None:
    """扫描所有概念页面，将指向 old_category 的链接更新为 new_category。

    在 maybe_create_subcategory 迁移文章时调用，确保概念页面链接不会因
    文章移入子目录而断裂。
    """
    escaped_title = re.escape(article_title)
    # 匹配旧路径的链接：- [[旧category/safe_title|display]]
    old_pattern = re.compile(
        r"(- \[\[)" + re.escape(old_category) + r"/([^\]]*?" + escaped_title + r"[^\]]*?\]\])"
    )

    for concept_file in concept_dir.glob("*.md"):
        try:
            content = concept_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not old_pattern.search(content):
            continue
        new_content = old_pattern.sub(r"\g<1>" + new_category + r"/\2", content)
        if new_content != content:
            atomic_write(concept_file, new_content)


def _fix_links_batch(
    articles_dir: Path,
    article_titles: list[str],
    old_category: str,
    new_category: str,
) -> None:
    """单次遍历所有文章和归档文件，批量更新链接。

    合并了原 _fix_article_links 和 _fix_archive_links 的功能，
    将 O(M*N) I/O 降为 O(N)，同时包含异常保护。

    使用逐链接提取 + 精确文件名匹配，避免正则 alternation 的子串匹配问题
    （如 article_titles=['AI'] 误伤 [[Agent/AI Agent|AI Agent]]）。
    """
    if not article_titles:
        return

    title_set = set(article_titles)
    escaped_category = re.escape(old_category)
    # 匹配 old_category 下的所有 wikilink，提取 link_target 和可选的 display 部分
    link_pattern = re.compile(r"\[\[" + escaped_category + r"/([^\]|]+?)(\|[^\]]*?)?\]\]")

    def _replace_link(re_match: re.Match[str]) -> str:
        link_target = re_match.group(1)
        rest = re_match.group(2) or ""
        if link_target in title_set:
            return f"[[{new_category}/{link_target}{rest}]]"
        return re_match.group(0)

    for category_dir in articles_dir.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        for md_file in category_dir.rglob("*.md"):
            if md_file.name == "_MOC.md":
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            new_content = link_pattern.sub(_replace_link, content)
            if new_content != content:
                atomic_write(md_file, new_content)


def _migrate_articles_to_subdir(
    processed: dict[str, Any],
    category: str,
    sub_topic: str,
    articles_dir: Path,
    sub_dir: Path,
    moc_file: Path,
    concept_dir: Path | None = None,
) -> None:
    """将已有文章迁移到子目录，同步更新概念页面中的链接。"""
    moc_content = moc_file.read_text(encoding="utf-8")
    new_entries: list[str] = []
    new_category = f"{category}/{sub_topic}"
    migrated_titles: list[str] = []

    for _article_id, record in processed.items():
        if not isinstance(record, dict):
            continue
        if (
            record.get("status") != "done"
            or record.get("category") != category
            or record.get("sub_topic") != sub_topic
        ):
            continue

        old_path = Path(record.get("file", ""))
        if not old_path.exists() or old_path.parent != articles_dir / category:
            continue

        new_path = sub_dir / old_path.name
        if new_path.exists():
            print(f"  警告: 目标文件已存在，跳过迁移: {new_path}")
            continue
        old_path.rename(new_path)
        record["file"] = str(new_path)

        # 更新 processed.json 中的 category 字段
        record["category"] = new_category

        # 更新文件内的 category 字段
        content = new_path.read_text(encoding="utf-8")
        old_cat = f'category: "{category}"'
        new_cat = f'category: "{new_category}"'
        if old_cat in content:
            atomic_write(new_path, content.replace(old_cat, new_cat))

        article_title = record.get("title", old_path.stem)
        migrated_titles.append(article_title)

        # 累积子目录 MOC 条目（使用完整路径）
        safe_title = old_path.stem
        original_title = record.get("title", safe_title)
        display = escape_display(original_title if original_title else safe_title)
        wikilink = f"[[{new_category}/{safe_title}|{display}]]"
        entry = f"- {record.get('processed_at', '')[:10]} {wikilink}"
        if entry not in moc_content:
            new_entries.append(entry)

    if new_entries:
        atomic_write(moc_file, moc_content.rstrip() + "\n" + "\n".join(new_entries) + "\n")

    # 批量更新链接（概念页面 + 文章/归档文件），单次遍历替代逐篇调用
    if migrated_titles:
        if concept_dir and concept_dir.exists():
            for title in migrated_titles:
                _fix_concept_links(concept_dir, title, category, new_category)
        _fix_links_batch(articles_dir, migrated_titles, category, new_category)


def _update_parent_moc(articles_dir: Path, category: str, sub_topic: str) -> None:
    """在父分类 MOC 中添加子目录文件夹条目（含子目录文章）。"""
    parent_moc = articles_dir / category / "_MOC.md"
    if not parent_moc.exists():
        return

    # 读取子目录 MOC，收集文章条目
    sub_moc = articles_dir / category / sub_topic / "_MOC.md"
    child_entries: list[str] = []
    if sub_moc.exists():
        _, sub_folders, sub_standalone = _parse_moc_content(sub_moc.read_text(encoding="utf-8"))
        for _, articles in sub_folders:
            child_entries.extend(articles)
        child_entries.extend(sub_standalone)

    # 构建文件夹条目：链接到子目录 MOC
    folder_display = escape_display(sub_topic)
    folder_entry = f"- 📁 [[{category}/{sub_topic}/_MOC|{folder_display}]]"

    # 读取并更新父 MOC
    content = parent_moc.read_text(encoding="utf-8")
    title_line, folder_groups, standalone = _parse_moc_content(content)

    # 检查该文件夹是否已存在
    folder_exists = any(
        f"/{sub_topic}/" in fl or f"|{sub_topic}]]" in fl for fl, _ in folder_groups
    )

    if not folder_exists:
        # 添加新文件夹及其子文章
        folder_groups.append((folder_entry, child_entries))

    # 始终清理 standalone 中已在文件夹组中的旧条目，避免重复
    all_folder_titles: set[str] = set()
    for _, articles in folder_groups:
        for art_entry in articles:
            for m in re.finditer(r"\[\[[^\]]*?/([^\]|]+?)(?:\|[^\]]*?)?\]\]", art_entry):
                all_folder_titles.add(m.group(1))
    if all_folder_titles:
        # 用正则精确匹配 wikilink 目标中的文件名段，避免子串误伤
        alternation = "|".join(re.escape(t) for t in all_folder_titles)
        folder_pat = re.compile(r"\[\[[^\]]*?/(?:" + alternation + r")(?:\||\]\])")
        standalone = [e for e in standalone if not folder_pat.search(e)]

    new_content = _rebuild_moc(title_line, folder_groups, standalone)
    if new_content != content:
        atomic_write(parent_moc, new_content)


# ---------------------------------------------------------------------------
# 知识库扫描
# ---------------------------------------------------------------------------


def scan_existing_content(vault_path: Path, articles_dir_name: str) -> tuple[list[str], list[str]]:
    """扫描知识库中已有的文章和概念，用于相关主题关联。"""
    articles_base = vault_path / articles_dir_name
    existing_articles: list[str] = []
    existing_concepts: list[str] = []

    if articles_base.exists():
        for category_dir in articles_base.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue
            for md_file in category_dir.glob("*.md"):
                if md_file.name != "_MOC.md":
                    existing_articles.append(md_file.stem)

    concept_dir = articles_base / "概念"
    if concept_dir.exists():
        for md_file in concept_dir.glob("*.md"):
            if md_file.name != "_MOC.md":
                existing_concepts.append(md_file.stem)

    return existing_articles, existing_concepts


# ---------------------------------------------------------------------------
# 按日归档
# ---------------------------------------------------------------------------


def update_daily_archive(
    vault_path: Path,
    date_str: str,
    title: str,
    category: str,
    summary: str,
) -> None:
    """更新按日归档文件，按分类分组显示。"""
    # 解析日期：2026-06-05 -> 26/06/05
    parts = date_str.split("-")
    if len(parts) != 3:
        return
    yy, mm, dd = parts[0][2:], parts[1], parts[2]

    # 归档文件路径
    articles_dir = vault_path / "公众号文章"
    archive_dir = articles_dir / "Z归档" / yy / mm
    archive_file = archive_dir / f"{dd}.md"

    category = sanitize_path_segment(category)
    safe_title = sanitize_path_segment(title)
    display = escape_display(title)
    wikilink = f"[[{category}/{safe_title}|{display}]]"

    # 读取已有条目，追加新条目，去重
    entries: list[tuple[str, str]] = []  # (category, entry_line)
    if archive_file.exists():
        for cat, line in _parse_archive_entries(archive_file.read_text(encoding="utf-8")):
            entries.append((cat, line))

    new_entry = f"- {wikilink}\n  {summary}"
    # 去重：检查 wikilink 是否已存在
    if any(wikilink in line for _, line in entries):
        return

    entries.append((category, new_entry))

    # 按分组重新生成文件
    content = _build_grouped_archive(date_str, entries)

    atomic_write(archive_file, content)


def _parse_archive_entries(content: str) -> list[tuple[str, str]]:
    """从归档文件内容中解析已有条目，返回 (category, entry_line) 列表。

    格式：## Category 分组 + 缩进摘要。
    """
    entries: list[tuple[str, str]] = []
    current_cat = ""
    entry_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if entry_lines and current_cat:
                entries.append((current_cat, "\n".join(entry_lines)))
            current_cat = line[3:].strip()
            entry_lines = []
        elif line.startswith("- ") and current_cat:
            if entry_lines:
                entries.append((current_cat, "\n".join(entry_lines)))
            entry_lines = [line]
        elif line.startswith("  ") and entry_lines:
            entry_lines.append(line)

    if entry_lines and current_cat:
        entries.append((current_cat, "\n".join(entry_lines)))

    return entries


def _build_grouped_archive(date_str: str, entries: list[tuple[str, str]]) -> str:
    """将条目按分类分组，生成归档文件内容。"""
    # 按分类分组，保持插入顺序
    groups: dict[str, list[str]] = {}
    for cat, line in entries:
        groups.setdefault(cat, []).append(line)

    lines = [f"# {date_str} 文章归档\n"]
    for cat, items in groups.items():
        lines.append(f"## {cat}")
        lines.extend(items)
        lines.append("")  # 分组间空行

    return "\n".join(lines)
