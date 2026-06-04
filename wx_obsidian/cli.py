"""CLI 入口：编排抓取→处理→输出的完整流程。"""

from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from wx_obsidian.config import load_config, load_processed, save_processed
from wx_obsidian.output.validator import validate_and_fix
from wx_obsidian.output.vault import (
    ensure_category,
    ensure_concept_page,
    maybe_create_subcategory,
    scan_existing_content,
    update_moc,
)
from wx_obsidian.processing.images import extract_images_with_context, insert_images_into_markdown
from wx_obsidian.processing.llm import summarize_article
from wx_obsidian.processing.markdown import generate_markdown, remove_non_cdn_images
from wx_obsidian.sources.rss import fetch_article_content_and_images, fetch_articles


# ---------------------------------------------------------------------------
# 单篇文章处理
# ---------------------------------------------------------------------------


def _extract_article_info(article: dict[str, Any]) -> dict[str, str]:
    """从原始文章数据中提取标准化字段。"""
    date = article.get("date_published", "") or ""
    if isinstance(date, str) and len(date) > 10:
        date = date[:10]
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    return {
        "id": str(article["id"]),
        "title": article.get("title", "无标题"),
        "account_name": article.get("_account_name", "未知"),
        "author": article.get("author", ""),
        "date": date,
        "url": article.get("url", ""),
    }


def _extract_content(
    article: dict[str, Any],
) -> tuple[str, list[dict[str, str]]]:
    """提取并清理文章正文内容，返回 (纯文本, 带上下文的图片列表)。"""
    raw_content = article.get("content", "")
    images = extract_images_with_context(raw_content)
    content = re.sub(r"<[^>]+>", " ", raw_content)
    content = re.sub(r"\s+", " ", content).strip()

    if len(content) < 50:
        url = article.get("url", "")
        if url:
            print("  Feed 无内容，从 URL 抓取...")
            content, body_html = fetch_article_content_and_images(url)
            if body_html:
                images = extract_images_with_context(body_html)

    return content, images


def _process_single_article(
    article: dict[str, Any],
    config: dict[str, Any],
    processed: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    existing_articles: list[str],
    existing_concepts: list[str],
) -> tuple[str, list[str]]:
    """处理单篇文章：抓取 → 总结 → 生成 → 写入。

    Returns:
        (safe_title, new_concept_names) — 用于调用方增量更新 existing 列表。
    """
    info = _extract_article_info(article)
    article_id = info["id"]

    print(f"\n处理: [{info['account_name']}] {info['title']}")

    content, images = _extract_content(article)
    if len(content) < 50:
        print("  跳过：内容过短或为空")
        processed[article_id] = {
            "title": info["title"],
            "status": "skipped",
            "reason": "no_content",
        }
        return ("", [])

    # DeepSeek 总结
    try:
        summary_data = summarize_article(
            info["title"],
            content,
            info["account_name"],
            existing_articles,
            existing_concepts,
        )
    except (requests.RequestException, ValueError) as e:
        print(f"  DeepSeek API 调用失败: {e}")
        processed[article_id] = {
            "title": info["title"],
            "status": "error",
            "reason": str(e),
        }
        return ("", [])

    if not summary_data:
        print("  总结解析失败")
        processed[article_id] = {
            "title": info["title"],
            "status": "error",
            "reason": "parse_failed",
        }
        return ("", [])

    # 写入文件（过滤路径安全字符）
    category = re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("category", "其他"))
    sub_topic = (
        re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("sub_topic", ""))
        if summary_data.get("sub_topic")
        else ""
    )
    ensure_category(vault_path, config, category, articles_dir)

    md_content = generate_markdown(
        info["title"],
        info["account_name"],
        info["author"],
        info["date"],
        info["url"],
        summary_data,
        valid_topics=existing_articles + existing_concepts,
    )

    md_content = remove_non_cdn_images(md_content)
    md_content = insert_images_into_markdown(md_content, images)

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", info["title"])[:100]
    category_dir = articles_dir / category
    category_dir.mkdir(parents=True, exist_ok=True)
    file_path = category_dir / f"{safe_title}.md"

    md_content, format_issues = validate_and_fix(md_content)
    if format_issues:
        print(f"  格式校验: {len(format_issues)} 个问题已修复")

    file_path.write_text(md_content, encoding="utf-8")

    # 更新关联数据
    new_concept_names: list[str] = []
    for concept in summary_data.get("concepts", []):
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", concept.get("name", "未知概念"))
        new_concept_names.append(safe_name)
        ensure_concept_page(
            vault_path, safe_name, concept.get("description", ""), articles_dir
        )

    update_moc(vault_path, category, safe_title, info["date"], articles_dir)

    processed[article_id] = {
        "title": info["title"],
        "status": "done",
        "category": category,
        "sub_topic": sub_topic,
        "file": str(file_path),
        "processed_at": datetime.now().isoformat(),
    }

    maybe_create_subcategory(vault_path, config, processed, category, sub_topic)
    print(f"  完成 → {category}/{safe_title}.md")
    return (safe_title, new_concept_names)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    parser.add_argument(
        "--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）"
    )
    return parser.parse_args()


def main() -> None:
    """主流程：拉取文章 → 总结 → 写入 Obsidian。"""
    args = _parse_args()

    config = load_config()
    processed = load_processed()
    vault_path = Path(config["obsidian"]["vault_path"])
    articles_dir = vault_path / config["obsidian"]["articles_dir"]

    print("正在从 WeWe RSS 获取文章...")
    try:
        articles = fetch_articles(config)
    except requests.RequestException as e:
        print(f"获取文章失败: {e}")
        print("请确认 WeWe RSS 已启动 (http://localhost:4000) 并已登录微信读书")
        sys.exit(1)

    new_articles = [
        a for a in articles if a.get("id") and str(a["id"]) not in processed
    ]
    if args.limit > 0:
        new_articles = new_articles[: args.limit]
    print(f"共获取 {len(articles)} 篇文章，其中 {len(new_articles)} 篇待处理")

    existing_articles_list, existing_concepts_list = scan_existing_content(
        vault_path, config["obsidian"]["articles_dir"]
    )

    for article in new_articles:
        new_title, new_concepts = _process_single_article(
            article,
            config,
            processed,
            vault_path,
            articles_dir,
            existing_articles_list,
            existing_concepts_list,
        )
        if new_title:
            existing_articles_list.append(new_title)
        existing_concepts_list.extend(new_concepts)
        save_processed(processed)
        time.sleep(1)
    done_count = sum(1 for v in processed.values() if v.get("status") == "done")
    print(f"\n处理完成！共处理 {done_count} 篇文章")
