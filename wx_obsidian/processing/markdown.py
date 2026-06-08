"""Markdown 生成：frontmatter、正文组装、假图片清除。"""

from __future__ import annotations

import re
from typing import Any

# 预编译正则：匹配非微信 CDN 的 markdown 图片链接
RE_NON_CDN_IMAGE = re.compile(r"!\[[^\]]*\]\((?!https?://mmbiz)[^)]+\)")


def remove_non_cdn_images(md: str) -> str:
    """清除 markdown 中非微信 CDN 的图片链接（LLM 幻觉生成的假图片）。"""
    return RE_NON_CDN_IMAGE.sub("", md)


def generate_markdown(
    title: str,
    account_name: str,
    author: str,
    date: str,
    url: str,
    summary_data: dict[str, Any],
    valid_topics: list[str] | None = None,
) -> str:
    """生成 Obsidian Markdown 文件内容。

    Args:
        valid_topics: 已有的文章/概念标题列表，用于过滤 related_topics。
            若提供，不在列表中的 related_topic 将被丢弃，防止死链接。
    """
    category = summary_data.get("category", "其他")
    sub_topic = summary_data.get("sub_topic", "")
    summary = summary_data.get("summary", "")
    key_points: list[str] = summary_data.get("key_points", [])
    concepts: list[dict[str, str]] = summary_data.get("concepts", [])
    tags: list[str] = summary_data.get("tags", [])
    related: list[str] = summary_data.get("related_topics", [])
    body_sections: list[dict[str, str]] = summary_data.get("body_sections", [])

    if valid_topics is not None:
        valid_set = set(valid_topics)
        related = [r for r in related if r in valid_set]

    frontmatter = _build_frontmatter(
        title, account_name, author, date, url, category, sub_topic, tags
    )

    points_md = "\n".join(f"- {p}" for p in key_points)

    def _clean_concept_name(raw: str) -> str:
        """去除概念名中可能由 LLM 添加的双向链接标记。"""
        return re.sub(r"[\[\]]", "", raw).strip()

    concepts_md = "\n".join(
        f"- [[{_clean_concept_name(c.get('name', '未知概念'))}]]：{c.get('description', '')}"
        for c in concepts
    )
    related_lines: list[str] = []
    for r in related:
        clean_r = re.sub(r"[\[\]]", "", r).strip()
        safe = re.sub(r'[<>:"/\\|?*]', "_", clean_r)[:100]
        related_lines.append(f"- [[{safe}|{clean_r}]]" if safe != clean_r else f"- [[{clean_r}]]")
    related_md = "\n".join(related_lines)

    body_parts: list[str] = []
    for section in body_sections:
        heading = section.get("heading", "")
        body_content = section.get("content", "")
        body_content = body_content.replace("\\n", "\n")
        body_parts.append(f"\n## {heading}\n\n{body_content}\n")
    body_md = "".join(body_parts)

    return f"""{frontmatter}

## 摘要
{summary}

## 核心观点
{points_md}
{body_md}
## 关键概念
{concepts_md}

## 相关主题
{related_md}
"""


def _build_frontmatter(
    title: str,
    account_name: str,
    author: str,
    date: str,
    url: str,
    category: str,
    sub_topic: str,
    tags: list[str],
) -> str:
    """构建 YAML frontmatter。"""
    tags_str = ", ".join(t.replace(" ", "_") for t in tags)
    sub_topic_line = f'\nsub_topic: "{sub_topic}"' if sub_topic else ""
    return f"""---
title: "{title.replace('"', "'")}"
source: "{account_name}"
author: "{author or account_name}"
date: {date}
tags: [{tags_str}]
category: "{category}"{sub_topic_line}
url: "{url}"
---"""
