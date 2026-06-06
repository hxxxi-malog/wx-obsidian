"""CLI 入口：编排抓取→处理→输出的完整流程。"""

from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from wx_obsidian.batch import ArchiveWriter, BatchProcessor, ResultCollector
from wx_obsidian.config import (
    load_config,
    load_processed,
    load_vision_config,
    save_last_fetch_date,
    save_processed,
)
from wx_obsidian.output.validator import validate_and_fix
from wx_obsidian.output.vault import (
    ensure_category,
    ensure_concept_page,
    maybe_create_subcategory,
    scan_existing_content,
    update_moc,
)
from wx_obsidian.processing.images import extract_images_with_context, insert_images_into_markdown
from wx_obsidian.processing.llm import refine_with_images, summarize_article
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
    try:
        article = ctx.article
        info = _extract_article_info(article)
        ctx.processed["__info"] = info
        ctx.processed["article_id"] = info["id"]

        print(f"\n处理: [{info['account_name']}] {info['title']}")

        content, images = _extract_content(article)
    except (KeyError, requests.RequestException, OSError) as e:
        article_id = ctx.article.get("id", "unknown")
        ctx.processed["article_id"] = str(article_id)
        ctx.processed["__info"] = {"title": "未知", "account_name": "未知"}
        ctx.processed["__skip"] = {"status": "error", "reason": str(e)}
        logger.warning("FetchStage 失败: %s", e, exc_info=True)
        return ctx

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


def _llm_pass1_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 3: Pass 1 — 纯文本生成结构化笔记（不看图片）。"""
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


def _llm_pass2_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 4: Pass 2 — 结合图片描述修订正文，嵌入 [IMG:N] 占位符。"""
    if ctx.processed.get("__skip"):
        return ctx

    # 无图片描述时跳过 Pass 2
    if not ctx.image_descriptions:
        return ctx

    body_sections = ctx.summary_data.get("body_sections", []) if ctx.summary_data else []
    if not body_sections:
        return ctx

    try:
        pass2_result = refine_with_images(
            ctx.content,
            body_sections,
            ctx.image_descriptions,
            ctx.images,
        )
    except (requests.RequestException, ValueError) as e:
        logger.warning("Pass 2 失败，降级到 Pass 1 结果: %s", e)
        return ctx

    if not pass2_result:
        logger.warning("Pass 2 解析失败，降级到 Pass 1 结果")
        return ctx

    # 用 Pass 2 的结果更新 body_sections 和 images
    if ctx.summary_data is None:
        return ctx
    if "body_sections" in pass2_result:
        ctx.summary_data["body_sections"] = pass2_result["body_sections"]
    if "images" in pass2_result:
        ctx.summary_data["images"] = pass2_result["images"]
        print(f"  图片决策: {len(pass2_result['images'])} 张有价值")

    return ctx


def _markdown_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 5: 生成 Markdown 并校验。"""
    if ctx.processed.get("__skip"):
        return ctx

    info = ctx.processed["__info"]
    summary_data = ctx.summary_data
    if summary_data is None:
        ctx.processed["__skip"] = {"status": "error", "reason": "no_summary_data"}
        return ctx

    try:
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
    except (ValueError, OSError) as e:
        ctx.processed["__skip"] = {"status": "error", "reason": str(e)}
        logger.warning("MarkdownStage 失败: %s", e, exc_info=True)
        return ctx

    ctx.md_content = md
    return ctx


def _image_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 6: 替换 [IMG:N] 占位符为图片 markdown，降级到关键词匹配。"""
    if ctx.processed.get("__skip") or not ctx.md_content:
        return ctx

    md = ctx.md_content
    summary_data = ctx.summary_data
    llm_images = summary_data.get("images", []) if summary_data else []

    if llm_images:
        for i, img in enumerate(llm_images, 1):
            url = img.get("url", "")
            purpose = img.get("purpose", "")
            if not url:
                continue
            desc = purpose[:20] if purpose else "图片"
            img_md = f"\n![{desc}]({url})\n"
            md = md.replace(f"[IMG:{i}]", img_md)
        # 清理未替换的占位符
        md = re.sub(r"\[IMG:\d+\]", "", md)
    else:
        if ctx.images:
            md = insert_images_into_markdown(md, ctx.images)

    ctx.md_content = md
    return ctx


def _write_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 7: 写入 vault 文件（知识图谱更新由串行阶段处理）。"""
    if ctx.processed.get("__skip"):
        skip_info = ctx.processed["__skip"]
        article_id = ctx.processed["article_id"]
        info = ctx.processed["__info"]
        ctx.processed["result"] = ("", [])
        ctx.processed["final"] = {
            article_id: {"title": info["title"], **skip_info},
        }
        return ctx

    info = ctx.processed["__info"]
    summary_data = ctx.summary_data
    if summary_data is None or ctx.md_content is None:
        article_id = ctx.processed.get("article_id", "unknown")
        ctx.processed["result"] = ("", [])
        ctx.processed["final"] = {
            article_id: {
                "title": info.get("title", "未知"),
                "status": "error",
                "reason": "missing_data",
            },
        }
        return ctx

    articles_dir = ctx.config["articles_dir"]

    category = re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("category", "其他"))
    sub_topic = (
        re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("sub_topic", ""))
        if summary_data.get("sub_topic")
        else ""
    )

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", info["title"])[:100]
    category_dir = articles_dir / category
    category_dir.mkdir(parents=True, exist_ok=True)
    file_path = category_dir / f"{safe_title}.md"

    file_path.write_text(ctx.md_content, encoding="utf-8")

    # 收集概念信息（由串行阶段创建概念页）
    concepts: list[dict[str, str]] = []
    for concept in summary_data.get("concepts", []):
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", concept.get("name", "未知概念"))
        concepts.append({
            "name": safe_name,
            "description": concept.get("description", ""),
        })

    article_id = ctx.processed["article_id"]
    ctx.processed["result"] = (safe_title, [c["name"] for c in concepts])
    ctx.processed["final"] = {
        article_id: {
            "title": info["title"],
            "status": "done",
            "category": category,
            "sub_topic": sub_topic,
            "file": str(file_path),
            "date": info["date"],
            "processed_at": datetime.now().isoformat(),
            "concepts": concepts,
        }
    }

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

    ctx = run_pipeline(
        ctx,
        [
            _fetch_stage,
            _vision_stage,
            _llm_pass1_stage,
            _llm_pass2_stage,
            _markdown_stage,
            _image_stage,
            _write_stage,
        ],
    )

    safe_title, new_concepts = ctx.processed.get("result", ("", []))
    return safe_title, new_concepts


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）")
    return parser.parse_args()


def _process_article_for_batch(
    article: dict[str, Any],
    config: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    existing_articles: list[str],
    existing_concepts: list[str],
    archive_writer: ArchiveWriter,
) -> dict[str, Any]:
    """处理单篇文章（用于批量处理）。"""
    processed: dict[str, Any] = {}
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

    ctx = run_pipeline(
        ctx,
        [
            _fetch_stage,
            _vision_stage,
            _llm_pass1_stage,
            _llm_pass2_stage,
            _markdown_stage,
            _image_stage,
            _write_stage,
        ],
    )

    # 更新归档（线程安全）
    if ctx.summary_data and ctx.md_content:
        info = ctx.processed.get("__info", {})
        category = ctx.summary_data.get("category", "其他")
        summary = ctx.summary_data.get("summary", "")
        archive_writer.update_archive(
            vault_path, info.get("date", ""), info.get("title", ""), category, summary
        )

    # 返回处理结果
    article_id = article.get("id", "unknown")
    result: dict[str, Any] = {}
    if ctx.processed.get("__skip"):
        skip_info = ctx.processed["__skip"]
        result = {
            "article_id": str(article_id),
            "title": article.get("title", "未知"),
            **skip_info,
        }
    elif ctx.processed.get("final"):
        result = list(ctx.processed["final"].values())[0]
        result["article_id"] = str(article_id)
    else:
        result = {
            "article_id": str(article_id),
            "title": article.get("title", "未知"),
            "status": "error",
            "reason": "unknown",
        }
    return result


def _update_knowledge_graph(
    config: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    processed: dict[str, Any],
) -> None:
    """串行阶段：更新知识图谱（MOC、概念页、子目录）。"""
    print("\n更新知识图谱...")

    # 重新扫描所有文章和概念
    existing_articles_list, existing_concepts_list = scan_existing_content(
        vault_path, config["obsidian"]["articles_dir"]
    )

    # 遍历所有已处理的文章，更新知识图谱
    for _article_id, record in processed.items():
        if not isinstance(record, dict) or record.get("status") != "done":
            continue

        category = record.get("category", "")
        sub_topic = record.get("sub_topic", "")
        safe_title = Path(record.get("file", "")).stem
        date = record.get("date", record.get("processed_at", "")[:10])

        if not category or not safe_title:
            continue

        # 确保分类存在
        ensure_category(vault_path, config, category, articles_dir)

        # 更新 MOC
        update_moc(vault_path, category, safe_title, date, articles_dir)

        # 创建概念页
        for concept in record.get("concepts", []):
            concept_name = concept.get("name", "")
            concept_desc = concept.get("description", "")
            if concept_name:
                ensure_concept_page(vault_path, concept_name, concept_desc, articles_dir)

        # 创建子目录（如果需要）
        maybe_create_subcategory(vault_path, config, processed, category, sub_topic)

    print("知识图谱更新完成")


def main() -> None:
    """主流程：拉取文章 → 总结 → 写入 Obsidian。"""
    # 注册信号处理器，实现优雅关闭
    shutdown_requested = False
    processor: BatchProcessor | None = None

    def _signal_handler(signum: int, frame: Any) -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            print("\n强制退出...")
            sys.exit(1)
        shutdown_requested = True
        print(f"\n收到中断信号 ({signum})，正在优雅关闭...")
        if processor:
            processor.request_shutdown()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

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

    # 增量抓取：只处理最近 7 天的文章（date_published 为空时也包含）
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    new_articles = [
        a
        for a in articles
        if a.get("id")
        and str(a["id"]) not in processed
        and (not a.get("date_published") or a.get("date_published", "")[:10] >= seven_days_ago)
    ]
    if args.limit > 0:
        new_articles = new_articles[: args.limit]
    print(f"共获取 {len(articles)} 篇文章，其中 {len(new_articles)} 篇待处理")

    if not new_articles:
        print("没有新文章需要处理")
        return

    # 启动时快照 existing_articles / existing_concepts
    existing_articles_list, existing_concepts_list = scan_existing_content(
        vault_path, config["obsidian"]["articles_dir"]
    )

    # 创建归档写入器
    archive_writer = ArchiveWriter()

    # 并行阶段：处理文章
    print(f"\n开始并行处理 {len(new_articles)} 篇文章...")
    result_collector = ResultCollector()

    # 预构建 article_id -> article 映射
    id_to_article = {str(a.get("id")): a for a in new_articles}

    def on_article_complete(result: dict[str, Any]) -> None:
        """每篇文章完成时的回调。"""
        article_id = result.get("article_id", "unknown")
        result_collector.add_result(article_id, result)
        # 每篇完成后原子保存 processed.json
        processed[article_id] = result
        save_processed(processed)

    processor = BatchProcessor()
    with processor:
        results = processor.process_articles(
            new_articles,
            lambda article: _process_article_for_batch(
                article,
                config,
                vault_path,
                articles_dir,
                existing_articles_list,
                existing_concepts_list,
                archive_writer,
            ),
            on_complete=on_article_complete,
        )

    # 计算最新文章日期
    latest_date = ""
    for result in results:
        article_id = result.get("article_id", "unknown")
        article = id_to_article.get(article_id)
        if article:
            article_date = article.get("date_published", "")[:10]
            if article_date and article_date > latest_date:
                latest_date = article_date

    print(f"\n并行处理完成，共处理 {len(results)} 篇文章")

    # 串行阶段：更新知识图谱
    _update_knowledge_graph(config, vault_path, articles_dir, processed)

    # 更新最后抓取日期
    if latest_date:
        save_last_fetch_date(latest_date)
        print(f"已更新最后抓取日期: {latest_date}")

    done_count = sum(
        1 for v in processed.values() if isinstance(v, dict) and v.get("status") == "done"
    )
    print(f"\n处理完成！共处理 {done_count} 篇文章")
