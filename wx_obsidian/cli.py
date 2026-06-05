"""CLI 入口：编排抓取→处理→输出的完整流程。"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from wx_obsidian.config import load_config, load_processed, load_vision_config, save_processed
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
from wx_obsidian.processing.models import PipelineContext
from wx_obsidian.processing.pipeline import run_pipeline
from wx_obsidian.processing.vision import describe_images
from wx_obsidian.sources.rss import fetch_article_content_and_images, fetch_articles

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Pipeline Stage 函数
# ---------------------------------------------------------------------------


def _fetch_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 1: 提取文章信息和正文内容。"""
    article = ctx.article
    info = _extract_article_info(article)
    ctx.processed["__info"] = info
    ctx.processed["article_id"] = info["id"]

    print(f"\n处理: [{info['account_name']}] {info['title']}")

    content, images = _extract_content(article)
    if len(content) < 50:
        print("  跳过：内容过短或为空")
        ctx.processed["__skip"] = {"status": "skipped", "reason": "no_content"}
        return ctx

    ctx.content = content
    ctx.images = images
    return ctx


def _vision_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 2: 调用多模态 Vision API 生成图片描述。失败时降级到纯文本。"""
    if ctx.processed.get("__skip") or not ctx.images:
        return ctx

    vision_config = load_vision_config()
    if not vision_config:
        logger.info("VisionStage: VISION_API_KEY 未设置，跳过多模态")
        return ctx

    try:
        ctx.image_descriptions = describe_images(ctx.images, vision_config)
    except Exception:
        logger.warning("VisionStage 失败，降级到纯文本", exc_info=True)
        ctx.image_descriptions = None
    return ctx


def _llm_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 3: 调用 LLM 生成结构化笔记。"""
    if ctx.processed.get("__skip"):
        return ctx

    info = ctx.processed["__info"]
    ctx.config["existing_articles"] = ctx.config.get("existing_articles", [])
    ctx.config["existing_concepts"] = ctx.config.get("existing_concepts", [])

    try:
        ctx.summary_data = summarize_article(
            info["title"],
            ctx.content,
            info["account_name"],
            ctx.image_descriptions,
            ctx.config["existing_articles"],
            ctx.config["existing_concepts"],
        )
    except (requests.RequestException, ValueError) as e:
        print(f"  DeepSeek API 调用失败: {e}")
        ctx.processed["__skip"] = {"status": "error", "reason": str(e)}
        return ctx

    if not ctx.summary_data:
        print("  总结解析失败")
        ctx.processed["__skip"] = {"status": "error", "reason": "parse_failed"}
        return ctx
    return ctx


def _markdown_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 4: 生成 Markdown 并校验。"""
    if ctx.processed.get("__skip"):
        return ctx

    info = ctx.processed["__info"]
    summary_data = ctx.summary_data
    assert summary_data is not None

    valid_topics = ctx.config["existing_articles"] + ctx.config["existing_concepts"]

    md = generate_markdown(
        info["title"],
        info["account_name"],
        info["author"],
        info["date"],
        info["url"],
        summary_data,
        valid_topics=valid_topics,
    )
    md = remove_non_cdn_images(md)

    md, format_issues = validate_and_fix(md)
    if format_issues:
        print(f"  格式校验: {len(format_issues)} 个问题已修复")

    ctx.md_content = md
    return ctx


def _image_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 5: 按 LLM 决策插入图片，降级到关键词匹配。"""
    if ctx.processed.get("__skip") or not ctx.md_content:
        return ctx

    md = ctx.md_content
    summary_data = ctx.summary_data
    llm_images = summary_data.get("images", []) if summary_data else []

    if llm_images:
        valuable = [img for img in llm_images if img.get("valuable", True)]
        print(f"  图片决策: {len(valuable)} 张有价值, {len(llm_images) - len(valuable)} 张被过滤")
        for img in valuable:
            placement = img.get("placement", "")
            url = img.get("url", "")
            purpose = img.get("purpose", "")
            if not placement or not url:
                continue
            desc = purpose[:50] if purpose else "图片"
            img_md = f"\n![{desc}]({url})"
            result = _insert_at_heading(md, placement, img_md)
            if result is not None:
                md = result
    else:
        if ctx.images:
            md = insert_images_into_markdown(md, ctx.images)

    ctx.md_content = md
    return ctx


def _normalize_quotes(text: str) -> str:
    """统一中英文引号为 ASCII 引号，用于 heading 模糊匹配。"""
    for ch in ("\u201c", "\u201d", "\u201e", "\u201f", "\u300c", "\u300d", "\u300e", "\u300f"):
        text = text.replace(ch, '"')
    for ch in ("\u2018", "\u2019", "\u201a", "\u201b", "\u2039", "\u203a"):
        text = text.replace(ch, "'")
    return text


def _strip_heading_prefix(line: str) -> str:
    """去掉 markdown heading 的 # 前缀和多余空格。"""
    return re.sub(r"^#{1,6}\s*", "", line.strip())


def _insert_at_heading(md: str, heading: str, img_md: str) -> str | None:
    """在指定 heading 后插入图片 markdown。返回 None 表示 heading 未找到。"""
    lines = md.split("\n")
    normalized_heading = _normalize_quotes(heading.strip())
    for i, line in enumerate(lines):
        line_text = _normalize_quotes(_strip_heading_prefix(line))
        if line_text and line_text == normalized_heading:
            # 找到 heading，在其后第一个非空行之后插入
            insert_pos = i + 1
            for j in range(i + 1, len(lines)):
                if lines[j].strip():
                    insert_pos = j + 1
                    break
            lines.insert(insert_pos, img_md)
            return "\n".join(lines)
    logger.warning("ImageStage: 未找到章节 '%s'，跳过图片插入", heading)
    return None


def _write_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 6: 写入 vault + 更新 MOC + 概念页。"""
    if ctx.processed.get("__skip"):
        skip_info = ctx.processed["__skip"]
        article_id = ctx.processed["article_id"]
        info = ctx.processed["__info"]
        ctx.processed["result"] = ("", [])
        ctx.processed["final"] = {
            article_id: {"title": info["title"], **skip_info},
        }
        global_processed = ctx.config["global_processed"]
        global_processed.update(ctx.processed["final"])
        return ctx

    info = ctx.processed["__info"]
    summary_data = ctx.summary_data
    assert summary_data is not None
    assert ctx.md_content is not None

    config = ctx.config["config"]
    vault_path = ctx.config["vault_path"]
    articles_dir = ctx.config["articles_dir"]

    category = re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("category", "其他"))
    sub_topic = (
        re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("sub_topic", ""))
        if summary_data.get("sub_topic")
        else ""
    )
    ensure_category(vault_path, config, category, articles_dir)

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", info["title"])[:100]
    category_dir = articles_dir / category
    category_dir.mkdir(parents=True, exist_ok=True)
    file_path = category_dir / f"{safe_title}.md"

    file_path.write_text(ctx.md_content, encoding="utf-8")

    # 更新关联数据
    new_concept_names: list[str] = []
    for concept in summary_data.get("concepts", []):
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", concept.get("name", "未知概念"))
        new_concept_names.append(safe_name)
        ensure_concept_page(
            vault_path, safe_name, concept.get("description", ""), articles_dir
        )

    update_moc(vault_path, category, safe_title, info["date"], articles_dir)

    article_id = ctx.processed["article_id"]
    ctx.processed["result"] = (safe_title, new_concept_names)
    ctx.processed["final"] = {
        article_id: {
            "title": info["title"],
            "status": "done",
            "category": category,
            "sub_topic": sub_topic,
            "file": str(file_path),
            "processed_at": datetime.now().isoformat(),
        }
    }

    global_processed = ctx.config["global_processed"]
    global_processed.update(ctx.processed["final"])
    maybe_create_subcategory(vault_path, config, global_processed, category, sub_topic)
    print(f"  完成 → {category}/{safe_title}.md")
    return ctx


# ---------------------------------------------------------------------------
# 单篇文章处理
# ---------------------------------------------------------------------------


def _process_single_article(
    article: dict[str, Any],
    config: dict[str, Any],
    processed: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    existing_articles: list[str],
    existing_concepts: list[str],
) -> tuple[str, list[str]]:
    """处理单篇文章：pipeline 编排。

    Returns:
        (safe_title, new_concept_names) — 用于调用方增量更新 existing 列表。
    """
    ctx = PipelineContext(
        article=article,
        content="",
        images=[],
        config={
            "config": config,
            "vault_path": vault_path,
            "articles_dir": articles_dir,
            "existing_articles": existing_articles,
            "existing_concepts": existing_concepts,
            "global_processed": processed,
        },
        processed={},
    )

    ctx = run_pipeline(ctx, [
        _fetch_stage,
        _vision_stage,
        _llm_stage,
        _markdown_stage,
        _image_stage,
        _write_stage,
    ])

    safe_title, new_concepts = ctx.processed.get("result", ("", []))
    return safe_title, new_concepts


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
