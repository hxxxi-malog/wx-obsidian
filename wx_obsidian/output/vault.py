"""Obsidian Vault 操作：MOC 更新、概念页面、分类管理、子目录拆分。"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from wx_obsidian.config import SCRIPT_DIR, SUB_TOPIC_THRESHOLD


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
        _atomic_write(concept_file, content)

    if article_title:
        _append_related_article(concept_file, article_title, article_category)


def _append_related_article(concept_file: Path, article_title: str, article_category: str) -> None:
    """向概念页面的"相关文章"部分追加文章链接。

    按文章标题（显示文本）去重：若已存在同标题链接但 category 路径不同
    （例如文章被 maybe_create_subcategory 移入子目录后），自动更新链接路径。
    """
    content = concept_file.read_text(encoding="utf-8")
    safe_title = re.sub(r'[<>:"/\\|?*]', "_", article_title)[:100]
    if article_category:
        wikilink = f"- [[{article_category}/{safe_title}|{article_title}]]"
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
        _atomic_write(concept_file, content)
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

    _atomic_write(concept_file, content)


# ---------------------------------------------------------------------------
# MOC 更新
# ---------------------------------------------------------------------------


def update_moc(
    vault_path: Path,
    category: str,
    title: str,
    date: str,
    articles_dir: Path | None = None,
    original_title: str = "",
) -> None:
    """更新分类 MOC 文件，追加新文章链接。

    Args:
        title: safe_title（文件名），用作 wikilink 目标。
        original_title: 原始标题，用作 wikilink 显示文本。为空时直接用 title。
    """
    base = articles_dir or (vault_path / "公众号文章")
    category_dir = base / category
    category_dir.mkdir(parents=True, exist_ok=True)

    moc_file = category_dir / "_MOC.md"
    if not moc_file.exists():
        _atomic_write(moc_file, f"# {category}\n\n")

    content = moc_file.read_text(encoding="utf-8")
    if original_title and original_title != title:
        wikilink = f"[[{title}|{original_title}]]"
    else:
        wikilink = f"[[{title}]]"
    entry = f"- {date} {wikilink}"
    if entry not in content:
        content = content.rstrip() + f"\n{entry}"
        _atomic_write(moc_file, content)


# ---------------------------------------------------------------------------
# 分类管理
# ---------------------------------------------------------------------------


def ensure_category(
    vault_path: Path,
    config: dict[str, Any],
    category: str,
    articles_dir: Path | None = None,
) -> None:
    """确保分类存在：创建目录、MOC 文件，并追加到 config.yaml。"""
    if category in config["categories"]:
        return

    print(f"  发现新分类：{category}，自动创建...")

    base = articles_dir or (vault_path / "公众号文章")
    category_dir = base / category
    category_dir.mkdir(parents=True, exist_ok=True)

    moc_file = category_dir / "_MOC.md"
    if not moc_file.exists():
        _atomic_write(moc_file, f"# {category}\n\n")

    config["categories"].append(category)
    _append_category_to_config(category)

    root_moc = base / "_MOC.md"
    if root_moc.exists():
        content = root_moc.read_text(encoding="utf-8")
        if f"[[{category}]]" not in content:
            content = content.rstrip() + f"\n- [[{category}]]"
            _atomic_write(root_moc, content)


def _append_category_to_config(category: str) -> None:
    """向 config.yaml 的 categories 列表末尾追加新分类（保留原有注释和格式）。"""
    config_path = SCRIPT_DIR / "config.yaml"
    content = config_path.read_text(encoding="utf-8")

    # 找到 categories 列表的最后一个条目，在其后追加
    # 匹配 categories 块中最后一个以 "- " 开头的行
    pattern = re.compile(r"(^categories:\n(?:\s*-\s+.+\n)*)", re.MULTILINE)
    match = pattern.search(content)
    if match:
        categories_block = match.group(1)
        # 在最后一个条目后追加
        new_block = categories_block.rstrip() + f"\n- {category}"
        content = content[: match.start()] + new_block + content[match.end() :]
        config_path.write_text(content, encoding="utf-8")
    else:
        # fallback：如果解析失败，追加到文件末尾
        with open(config_path, "a", encoding="utf-8") as f:
            f.write(f"- {category}\n")


# ---------------------------------------------------------------------------
# 子目录拆分
# ---------------------------------------------------------------------------


def count_sub_topic_articles(processed: dict[str, Any], category: str, sub_topic: str) -> int:
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

    count = count_sub_topic_articles(processed, category, sub_topic)
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
        _atomic_write(moc_file, f"# {sub_topic}\n\n")

    concept_dir = articles_dir / "概念"
    _migrate_articles_to_subdir(
        processed, category, sub_topic, articles_dir, sub_dir, moc_file, concept_dir
    )
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
        content = concept_file.read_text(encoding="utf-8")
        if not old_pattern.search(content):
            continue
        new_content = old_pattern.sub(r"\g<1>" + new_category + r"/\2", content)
        if new_content != content:
            _atomic_write(concept_file, new_content)


def _fix_archive_links(
    articles_dir: Path,
    article_title: str,
    old_category: str,
    new_category: str,
) -> None:
    """扫描所有归档文件，将指向 old_category 的链接更新为 new_category。

    在 maybe_create_subcategory 迁移文章时调用，确保归档文件链接不会因
    文章移入子目录而断裂。
    """
    escaped_title = re.escape(article_title)
    old_pattern = re.compile(
        r"(- \[\[)" + re.escape(old_category) + r"/([^\]]*?" + escaped_title + r"[^\]]*?\]\])"
    )

    archive_dir = articles_dir / "Z归档"
    if not archive_dir.exists():
        return

    for archive_file in archive_dir.rglob("*.md"):
        content = archive_file.read_text(encoding="utf-8")
        if not old_pattern.search(content):
            continue
        new_content = old_pattern.sub(r"\g<1>" + new_category + r"/\2", content)
        if new_content != content:
            _atomic_write(archive_file, new_content)


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
        old_path.rename(new_path)
        record["file"] = str(new_path)

        # 更新文件内的 category 字段
        content = new_path.read_text(encoding="utf-8")
        old_cat = f'category: "{category}"'
        new_cat = f'category: "{category}/{sub_topic}"'
        if old_cat in content:
            _atomic_write(new_path, content.replace(old_cat, new_cat))

        # 更新概念页面和归档文件中的链接路径
        if concept_dir and concept_dir.exists():
            article_title = record.get("title", old_path.stem)
            _fix_concept_links(concept_dir, article_title, category, f"{category}/{sub_topic}")
            _fix_archive_links(articles_dir, article_title, category, f"{category}/{sub_topic}")

        # 累积子目录 MOC 条目
        safe_title = old_path.stem
        entry = f"- {record.get('processed_at', '')[:10]} [[{safe_title}]]"
        if entry not in moc_content:
            new_entries.append(entry)

    if new_entries:
        _atomic_write(moc_file, moc_content.rstrip() + "\n" + "\n".join(new_entries) + "\n")


def _update_parent_moc(articles_dir: Path, category: str, sub_topic: str) -> None:
    """在父分类 MOC 中添加子目录链接。"""
    parent_moc = articles_dir / category / "_MOC.md"
    if not parent_moc.exists():
        return

    content = parent_moc.read_text(encoding="utf-8")
    if f"[[{sub_topic}]]" not in content:
        content = content.rstrip() + f"\n- 📁 [[{sub_topic}]]"
        _atomic_write(parent_moc, content)


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

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)[:100]
    wikilink = f"[[{category}/{safe_title}|{safe_title}]]"

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

    # 原子写入
    archive_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=archive_dir, suffix=".tmp", prefix=".archive_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, archive_file)
    except BaseException:
        os.unlink(tmp_path)
        raise


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
