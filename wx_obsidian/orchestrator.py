"""核心编排器：TUI/CLI 共享的抓取→处理→输出流程。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from wx_obsidian.batch import ArchiveWriter, BatchProcessor
from wx_obsidian.config import load_processed, load_vision_config, save_processed
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import (
    AccountStatus,
    ConnectionTestResult,
    FailedArticle,
    Feed,
    HealthStatus,
    ProcessingResult,
    Statistics,
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
from wx_obsidian.processing.llm import summarize_article, validate_images_field
from wx_obsidian.processing.markdown import generate_markdown, remove_non_cdn_images
from wx_obsidian.processing.models import PipelineContext
from wx_obsidian.processing.pipeline import run_pipeline
from wx_obsidian.processing.similarity import compute_related
from wx_obsidian.processing.vision import describe_images
from wx_obsidian.sources.rss import fetch_article_content_and_images, fetch_articles
from wx_obsidian.wewe_rss import WeWeRSSClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline Stage 函数（从 cli.py 迁移）
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
            logger.info("Feed 无内容，从 URL 抓取: %s", url[:80])
            content, body_html = fetch_article_content_and_images(url)
            if body_html:
                images = extract_images_with_context(body_html)
        else:
            logger.warning("Feed 无内容且无 URL，跳过")

    return content, images


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

    full_config = ctx.config.get("config", {})
    vision_config = load_vision_config(config=full_config)
    if not vision_config:
        logger.info("VisionStage: VISION_API_KEY 未设置，跳过多模态")
        return ctx

    try:
        ctx.image_descriptions = describe_images(ctx.images, vision_config)
        if ctx.image_descriptions:
            for desc in ctx.image_descriptions:
                logger.info(
                    "Vision 图片: %s | type=%s is_content=%s desc=%s",
                    desc.url[:80],
                    desc.type,
                    desc.is_content,
                    desc.description[:60] if desc.description else "(空)",
                )
    except Exception:
        logger.warning("VisionStage 失败，降级到纯文本", exc_info=True)
        ctx.image_descriptions = None
    return ctx


def _llm_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 3: LLM 生成结构化笔记（含图片上下文）。"""
    if ctx.processed.get("__skip"):
        return ctx

    info = ctx.processed["__info"]
    ctx.config["existing_articles"] = ctx.config.get("existing_articles", [])
    ctx.config["existing_concepts"] = ctx.config.get("existing_concepts", [])

    full_config = ctx.config.get("config", {})

    try:
        ctx.summary_data = summarize_article(
            info["title"],
            ctx.content,
            info["account_name"],
            ctx.config["existing_articles"],
            ctx.config["existing_concepts"],
            config=full_config,
            image_descriptions=ctx.image_descriptions,
            images_with_context=ctx.images,
        )
    except (requests.RequestException, ValueError) as e:
        print(f"  DeepSeek API 调用失败: {e}")
        ctx.processed["__skip"] = {"status": "error", "reason": str(e)}
        return ctx

    if not ctx.summary_data:
        logger.warning("LLM 返回内容解析失败（详见 last_response.txt）: %s", info["title"])
        ctx.processed["__skip"] = {
            "status": "error",
            "reason": "LLM返回JSON解析失败，详见last_response.txt",
        }
        return ctx

    # 验证 images 字段
    if "images" in ctx.summary_data:
        ctx.summary_data["images"] = validate_images_field(ctx.summary_data["images"])
        if ctx.summary_data["images"]:
            print(f"  图片决策: {len(ctx.summary_data['images'])} 张有价值")
        else:
            content_imgs = [d for d in (ctx.image_descriptions or []) if d.is_content]
            print(f"  图片决策: LLM 选择不使用图片（Vision 识别 {len(content_imgs)} 张内容图）")
    else:
        print("  图片决策: LLM 未返回 images 字段")

    return ctx


def _normalize_llm_response(data: dict[str, Any]) -> None:
    """规范化 LLM 返回的 concepts/body_sections 字段，原地修改确保类型正确。"""
    # concepts 应为 list[dict]，LLM 可能返回 None / str / list[str]
    concepts = data.get("concepts") or []
    if isinstance(concepts, str):
        logger.warning("LLM 返回的 concepts 为字符串，自动规范化")
        data["concepts"] = [{"name": concepts, "description": ""}]
    elif isinstance(concepts, list) and any(not isinstance(c, dict) for c in concepts):
        logger.warning("LLM 返回的 concepts 包含非 dict 元素，自动规范化")
        data["concepts"] = [
            {"name": str(c), "description": ""}
            if isinstance(c, str)
            else {"name": "未知概念", "description": ""}
            for c in concepts
        ]
    elif not isinstance(concepts, list):
        logger.warning("LLM 返回的 concepts 类型异常 (%s)，重置为空列表", type(concepts).__name__)
        data["concepts"] = []

    # body_sections 应为 list[dict]，LLM 可能返回 None / str / list[str]
    sections = data.get("body_sections") or []
    if isinstance(sections, str):
        logger.warning("LLM 返回的 body_sections 为字符串，自动规范化")
        data["body_sections"] = [{"heading": "", "content": sections}]
    elif isinstance(sections, list) and any(not isinstance(s, dict) for s in sections):
        logger.warning("LLM 返回的 body_sections 包含非 dict 元素，自动规范化")
        data["body_sections"] = [
            {"heading": "", "content": str(s)}
            if isinstance(s, str)
            else {"heading": "", "content": ""}
            for s in sections
        ]
    elif not isinstance(sections, list):
        logger.warning(
            "LLM 返回的 body_sections 类型异常 (%s)，重置为空列表", type(sections).__name__
        )
        data["body_sections"] = []


def _markdown_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 5: 生成 Markdown 并校验。"""
    if ctx.processed.get("__skip"):
        return ctx

    info = ctx.processed["__info"]
    summary_data = ctx.summary_data
    if summary_data is None:
        ctx.processed["__skip"] = {"status": "error", "reason": "no_summary_data"}
        return ctx

    _normalize_llm_response(summary_data)

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

        # 结构性问题检测 + LLM 反馈修正
        try:
            from wx_obsidian.output.validator import detect_format_issues
            from wx_obsidian.processing.llm import fix_format_issues as _fix_format

            structural_issues = detect_format_issues(md)
            if structural_issues:
                full_config = ctx.config.get("config", {})
                logger.info("结构性格式问题，反馈 LLM 修正: %s", structural_issues)
                print(f"  结构性格式问题: {len(structural_issues)} 个，反馈 LLM 修正...")
                fixed_md = _fix_format(md, structural_issues, config=full_config)
                if fixed_md and fixed_md != md:
                    md, re_fix_issues = validate_and_fix(fixed_md)
                    if re_fix_issues:
                        print(f"  LLM 修正后再校验: {len(re_fix_issues)} 个问题已修复")
                    else:
                        print("  LLM 修正后格式校验通过")
                else:
                    print("  LLM 修正未产生变化，保留自动修复版本")
        except Exception as e:
            logger.warning("结构性格式修正失败（跳过）: %s", e)
    except (ValueError, OSError, AttributeError) as e:
        ctx.processed["__skip"] = {"status": "error", "reason": str(e)}
        logger.warning("MarkdownStage 失败: %s", e, exc_info=True)
        return ctx

    ctx.md_content = md
    return ctx


def _image_stage(ctx: PipelineContext) -> PipelineContext:
    """Stage 4: 替换 [IMG:N] 占位符为图片 markdown，清除残留占位符。"""
    if ctx.processed.get("__skip") or not ctx.md_content:
        return ctx

    md = ctx.md_content
    summary_data = ctx.summary_data
    llm_images = summary_data.get("images", []) if summary_data else []
    if not isinstance(llm_images, list):
        llm_images = []

    if llm_images:
        for i, img in enumerate(llm_images, 1):
            if not isinstance(img, dict):
                continue
            url = img.get("url", "")
            purpose = img.get("purpose", "")
            if not url:
                continue
            desc = purpose[:20] if purpose else "图片"
            img_md = f"\n![{desc}]({url})\n"
            md = md.replace(f"[IMG:{i}]", img_md)

    # 清除所有未替换的 [IMG:N] 占位符
    md = re.sub(r"\[IMG:\d+\]", "", md)

    # 未配视觉模型时，LLM 没有图片上下文，降级到关键词匹配
    if not llm_images and ctx.images and ctx.image_descriptions is None:
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

    concepts: list[dict[str, str]] = []
    for concept in summary_data.get("concepts") or []:
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", concept.get("name", "未知概念"))
        concepts.append(
            {
                "name": safe_name,
                "description": concept.get("description", ""),
            }
        )

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
            "summary": summary_data.get("summary", ""),
            "tags": summary_data.get("tags", []),
        }
    }

    print(f"  完成 → {category}/{safe_title}.md")
    return ctx


_PIPELINE_STAGES: list[Callable[[PipelineContext], PipelineContext]] = [
    _fetch_stage,
    _vision_stage,
    _llm_stage,
    _markdown_stage,
    _image_stage,
    _write_stage,
]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class Orchestrator:
    """核心编排器：抓取→处理→输出的完整流程。"""

    def __init__(
        self,
        config_manager: ConfigManager,
        wewe_rss: WeWeRSSClient,
    ) -> None:
        self._config_manager = config_manager
        self._wewe_rss = wewe_rss

    def get_health_status(self) -> HealthStatus:
        """检测所有组件健康状态。"""
        wewe = self._config_manager.test_wewe_rss_connection()
        llm = self._config_manager.test_llm_connection()
        vision = self._config_manager.test_vision_connection()

        vault_path_str = self._config_manager.get("obsidian.vault_path", "")
        vault: ConnectionTestResult | None = None
        if vault_path_str:
            vp = Path(vault_path_str)
            if vp.exists() and vp.is_dir():
                vault = ConnectionTestResult(success=True, latency_ms=0, message="Vault 路径存在")
            else:
                vault = ConnectionTestResult(
                    success=False, latency_ms=0, message=f"Vault 路径不存在: {vault_path_str}"
                )

        return HealthStatus(
            wewe_rss=wewe,
            llm_api=llm,
            vision_api=vision,
            vault_path=vault,
        )

    def get_statistics(self) -> Statistics:
        """从 processed.json 统计处理信息。"""
        processed = load_processed()
        categories: dict[str, int] = {}
        processed_count = 0
        failed_count = 0

        for _key, value in processed.items():
            if not isinstance(value, dict):
                continue
            status = value.get("status", "")
            if status == "done":
                processed_count += 1
                cat = value.get("category", "其他")
                categories[cat] = categories.get(cat, 0) + 1
            elif status == "error":
                failed_count += 1

        return Statistics(
            total_articles=processed_count + failed_count,
            processed_articles=processed_count,
            failed_articles=failed_count,
            categories=categories,
        )

    def get_failed_articles(self) -> list[FailedArticle]:
        """从 failed.json 读取失败记录。"""
        from wx_obsidian.config_manager import FAILED_FILE

        if not FAILED_FILE.exists():
            return []
        try:
            data = json.loads(FAILED_FILE.read_text(encoding="utf-8"))
            return [
                FailedArticle(
                    article_id=item.get("article_id", ""),
                    title=item.get("title", ""),
                    error=item.get("error", ""),
                    retry_count=item.get("retry_count", 0),
                )
                for item in data
                if isinstance(item, dict)
            ]
        except (json.JSONDecodeError, OSError):
            return []

    # -- WeWe RSS 代理方法（TUI 通过 orchestrator 调用） ----------------------

    def get_account_status(self) -> AccountStatus:
        """获取微信读书登录状态。"""
        return self._wewe_rss.get_account_status()

    def is_wewe_healthy(self) -> bool:
        """WeWe RSS 服务是否可达。"""
        return self._wewe_rss.is_healthy()

    def get_feeds(self) -> list[Feed]:
        """获取已添加的公众号列表。"""
        return self._wewe_rss.get_feeds()

    def add_feed(self, article_url: str) -> Feed | None:
        """通过文章链接添加公众号。"""
        return self._wewe_rss.add_feed(article_url)

    def delete_feed(self, feed_id: str) -> bool:
        """删除公众号。"""
        return self._wewe_rss.delete_feed(feed_id)

    def refresh_cookie(self) -> bool:
        """刷新微信读书 cookie。"""
        return self._wewe_rss.refresh_cookie()

    def get_login_url(self) -> str:
        """获取 WeWe RSS 登录页面 URL。"""
        return self._wewe_rss.get_login_url()

    async def fetch_and_process(
        self,
        limit: int = 0,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> list[ProcessingResult]:
        """核心抓取流程。异步包装，同步逻辑在线程池中执行。

        Args:
            limit: 最大处理篇数，0 表示不限制。
            on_progress: 进度回调 (title, completed, total)。在后台线程中调用。
        """
        return await asyncio.to_thread(self._fetch_and_process_sync, limit, on_progress)

    def _fetch_and_process_sync(
        self,
        limit: int = 0,
        on_progress: Callable[[str, int, int], None] | None = None,
    ) -> list[ProcessingResult]:
        """核心抓取流程（同步实现）。"""
        # 加载 .env 到 os.environ（确保 load_vision_config 等函数正常工作）
        self._load_env_to_environ()

        processed = load_processed()
        vault_path = Path(self._config_manager.get("obsidian.vault_path", ""))
        articles_dir_name = self._config_manager.get("obsidian.articles_dir", "公众号文章")
        articles_dir = vault_path / articles_dir_name

        # 构建 config dict（兼容需要 config 参数的旧函数）
        config: dict[str, Any] = {
            "wewe_rss": {
                "base_url": self._config_manager.get("wewe_rss.base_url", "http://localhost:4000")
            },
            "obsidian": {"vault_path": str(vault_path), "articles_dir": articles_dir_name},
            "categories": self._config_manager.get("categories", []),
            "llm": self._config_manager.get("llm", {}),
            "vision": self._config_manager.get("vision", {}),
        }

        # 获取文章列表
        try:
            articles = fetch_articles(config)
        except requests.RequestException as e:
            logger.error("获取文章失败: %s", e)
            return []

        # 增量过滤
        max_days = self._config_manager.get("fetch.max_days", 7)
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        new_articles = [
            a
            for a in articles
            if a.get("id")
            and str(a["id"]) not in processed
            and (not a.get("date_published") or a.get("date_published", "")[:10] >= cutoff)
        ]
        if limit > 0:
            new_articles = new_articles[:limit]

        if not new_articles:
            logger.info("没有新文章需要处理")
            return []

        total = len(new_articles)
        logger.info("开始处理 %d 篇文章", total)
        if on_progress:
            on_progress("_start", 0, total)

        # 扫描已有内容
        articles_dir_name = self._config_manager.get(
            "obsidian.articles_dir",
            "公众号文章",
        )
        existing_articles, existing_concepts = scan_existing_content(vault_path, articles_dir_name)

        # 创建归档写入器
        archive_writer = ArchiveWriter()

        # 批量处理
        id_to_article = {str(a.get("id")): a for a in new_articles}
        new_ids: set[str] = set()
        results_raw: list[dict[str, Any]] = []

        completed_count = 0

        def on_complete(result: dict[str, Any]) -> None:
            nonlocal completed_count
            article_id = result.get("article_id", "unknown")
            processed[article_id] = result
            new_ids.add(article_id)
            completed_count += 1
            if on_progress:
                title = result.get("title", "未知")
                on_progress(title, completed_count, total)

        max_workers = self._config_manager.get("fetch.max_workers", 5)
        executor = ThreadPoolExecutor(max_workers=max_workers)
        processor = BatchProcessor(executor=executor)
        with processor:
            results_raw = processor.process_articles(
                new_articles,
                lambda article: _process_single(
                    article,
                    config,
                    vault_path,
                    articles_dir,
                    existing_articles,
                    existing_concepts,
                    archive_writer,
                ),
                on_complete=on_complete,
            )

        # 更新最后抓取日期（加入 processed dict，一起保存）
        latest_date = ""
        for result in results_raw:
            article_id = result.get("article_id", "unknown")
            article = id_to_article.get(article_id)
            if article:
                article_date = article.get("date_published", "")[:10]
                if article_date and article_date > latest_date:
                    latest_date = article_date
        if latest_date:
            processed["last_fetch_date"] = latest_date

        # 保存 processed.json（统一写入新路径）
        save_processed(processed)

        # 自动重试失败文章（清除记录后重新抓取）
        failed_articles = [
            id_to_article[aid]
            for aid in new_ids
            if aid in id_to_article
            and isinstance(processed.get(aid), dict)
            and processed[aid].get("status") in ("error", "skipped")
        ]
        if failed_articles:
            logger.info("自动重试 %d 篇失败文章", len(failed_articles))
            for art in failed_articles:
                processed.pop(str(art.get("id", "")), None)
            save_processed(processed)

            retry_completed = 0

            def on_retry_complete(result: dict[str, Any]) -> None:
                nonlocal retry_completed
                article_id = result.get("article_id", "unknown")
                processed[article_id] = result
                new_ids.add(article_id)
                retry_completed += 1
                if on_progress:
                    title = result.get("title", "未知")
                    on_progress(title, retry_completed, len(failed_articles))

            with BatchProcessor(executor=executor) as retry_processor:
                retry_raw = retry_processor.process_articles(
                    failed_articles,
                    lambda article: _process_single(
                        article,
                        config,
                        vault_path,
                        articles_dir,
                        existing_articles,
                        existing_concepts,
                        archive_writer,
                    ),
                    on_complete=on_retry_complete,
                )
            # 合并重试结果
            results_raw.extend(retry_raw)
            save_processed(processed)

        executor.shutdown(wait=False)

        # 计算同批文章间的关联，回填相关主题
        _update_related_topics(processed, new_ids, on_progress)

        # 更新知识图谱（仅处理新文章）
        _update_knowledge_graph(config, vault_path, articles_dir, processed, new_ids, on_progress)

        # 按 article_id 去重，重试成功覆盖首次失败
        deduped: dict[str, dict[str, Any]] = {}
        for r in results_raw:
            aid = str(r.get("article_id", ""))
            if aid:
                deduped[aid] = r

        # 转换为 ProcessingResult
        results: list[ProcessingResult] = []
        for r in deduped.values():
            results.append(
                ProcessingResult(
                    article_id=str(r.get("article_id", "")),
                    title=r.get("title", ""),
                    status=r.get("status", "error"),
                    category=r.get("category"),
                    file_path=r.get("file"),
                    error=r.get("reason") or r.get("error"),
                )
            )

        logger.info("处理完成，共 %d 篇文章", len(results))
        return results

    async def retry_failed(self, article_ids: list[str]) -> list[ProcessingResult]:
        """重试失败文章。仅清除 status=error 的记录，然后重新抓取。"""
        processed = load_processed()
        cleared = 0

        for aid in article_ids:
            record = processed.get(aid)
            if isinstance(record, dict) and record.get("status") == "error":
                processed.pop(aid, None)
                cleared += 1

        if cleared == 0:
            logger.info("没有可重试的失败文章")
            return []

        save_processed(processed)
        logger.info("已清除 %d 条失败记录，开始重新抓取", cleared)

        # 重新抓取（仅处理被清除的失败文章，不处理其他新文章）
        return await self.fetch_and_process(limit=cleared)

    def _load_env_to_environ(self) -> None:
        """加载 .env 文件到 os.environ（确保 load_vision_config 等函数正常工作）。"""
        self._config_manager.ensure_env_loaded()


# ---------------------------------------------------------------------------
# 内部函数
# ---------------------------------------------------------------------------


def _process_single(
    article: dict[str, Any],
    config: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    existing_articles: list[str],
    existing_concepts: list[str],
    archive_writer: ArchiveWriter,
) -> dict[str, Any]:
    """处理单篇文章（复用 pipeline stage 函数）。"""
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
        },
        processed={},
    )

    ctx = run_pipeline(ctx, _PIPELINE_STAGES)

    # 更新归档
    if ctx.summary_data and ctx.md_content:
        info = ctx.processed.get("__info", {})
        category = ctx.summary_data.get("category", "其他")
        summary = ctx.summary_data.get("summary", "")
        archive_writer.update_archive(
            vault_path, info.get("date", ""), info.get("title", ""), category, summary
        )

    # 返回结果
    article_id = article.get("id", "unknown")
    if ctx.processed.get("__skip"):
        skip_info = ctx.processed["__skip"]
        return {"article_id": str(article_id), "title": article.get("title", "未知"), **skip_info}
    if ctx.processed.get("final"):
        result: dict[str, Any] = list(ctx.processed["final"].values())[0]
        result["article_id"] = str(article_id)
        return result
    return {
        "article_id": str(article_id),
        "title": article.get("title", "未知"),
        "status": "error",
        "reason": "unknown",
    }


def _update_knowledge_graph(
    config: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    processed: dict[str, Any],
    new_ids: set[str] | None = None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> None:
    """串行阶段：更新知识图谱。仅处理 new_ids 中的文章（如果提供）。"""
    # 统计待处理文章数
    pending = [
        (aid, rec)
        for aid, rec in processed.items()
        if isinstance(rec, dict)
        and rec.get("status") == "done"
        and (new_ids is None or aid in new_ids)
    ]
    total = len(pending)
    if on_progress and total > 0:
        on_progress("_kg", 0, total)

    seen_subcategory: set[tuple[str, str]] = set()
    for idx, (_article_id, record) in enumerate(pending, 1):
        category = record.get("category", "")
        sub_topic = record.get("sub_topic", "")
        safe_title = Path(record.get("file", "")).stem
        date = record.get("date", record.get("processed_at", "")[:10])

        if not category or not safe_title:
            continue

        try:
            if on_progress:
                on_progress(f"更新分类: {category}", idx, total)

            ensure_category(vault_path, config, category, articles_dir)
            update_moc(vault_path, category, safe_title, date, articles_dir)

            original_title = record.get("title", safe_title)
            for concept in record.get("concepts") or []:
                if not isinstance(concept, dict):
                    continue
                concept_name = concept.get("name", "")
                concept_desc = concept.get("description", "")
                if concept_name:
                    if on_progress:
                        on_progress(f"生成概念: {concept_name}", idx, total)
                    ensure_concept_page(
                        vault_path,
                        concept_name,
                        concept_desc,
                        articles_dir,
                        article_title=original_title,
                        article_category=category,
                    )

            key = (category, sub_topic)
            if sub_topic and key not in seen_subcategory:
                seen_subcategory.add(key)
                maybe_create_subcategory(vault_path, config, processed, category, sub_topic)
        except (OSError, ValueError, KeyError) as e:
            logger.warning("知识图谱更新失败 [%s/%s]: %s", category, safe_title, e, exc_info=True)

    if on_progress and total > 0:
        on_progress("_kg_done", total, total)


def _update_related_topics(
    processed: dict[str, Any],
    new_ids: set[str] | None,
    on_progress: Callable[[str, int, int], None] | None = None,
) -> None:
    """计算同批文章间的关联，回填 markdown 的相关主题。"""
    if not new_ids:
        return

    if on_progress:
        on_progress("_related", 0, 1)

    related_map = compute_related(processed, new_ids)

    updated = 0
    for article_id, related_titles in related_map.items():
        if not related_titles:
            continue
        record = processed.get(article_id)
        if not isinstance(record, dict):
            continue
        file_path_str = record.get("file", "")
        if not file_path_str:
            continue
        file_path = Path(file_path_str)
        if not file_path.exists():
            continue

        try:
            md = file_path.read_text(encoding="utf-8")
        except OSError:
            continue

        related_md = "\n".join(f"- [[{t}]]" for t in related_titles)
        new_md, count = re.subn(
            r"(## 相关主题\n)([^\n#]*)",
            r"\1" + related_md + "\n",
            md,
            flags=re.DOTALL,
        )
        if count > 0 and new_md != md:
            file_path.write_text(new_md, encoding="utf-8")
            updated += 1

    if updated > 0:
        logger.info("相关主题回填完成：%d 篇文章", updated)

    if on_progress:
        on_progress("_related_done", 1, 1)
