"""集成测试：单篇处理编排的数据流、降级路径、回归测试。

历史背景：单篇文章的编排原为 `wx_obsidian.cli._process_single_article`，
pipeline 改为可插拔 article source 后迁移到
`wx_obsidian.orchestrator._process_single`：它接收 config / vault_path /
articles_dir / archive_writer，并返回结果 dict（不再返回
`(safe_title, new_concepts)` 二元组，也不再直接写 processed.json）。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from wx_obsidian.batch import ArchiveWriter
from wx_obsidian.orchestrator import _process_single
from wx_obsidian.processing.images import insert_images_into_markdown
from wx_obsidian.processing.models import ImageDescription, PipelineContext
from wx_obsidian.processing.pipeline import run_pipeline

IMG = "https://mmbiz.qpic.cn/test.png"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_article(title: str = "测试文章", content_html: str = "<p>正文内容</p>") -> dict:
    """创建测试用文章数据。"""
    return {
        "id": "test_001",
        "title": title,
        "url": "https://mp.weixin.qq.com/s/test",
        "content": content_html,
        "date_published": "2026-01-01",
        "author": "作者",
        "_account_name": "测试公众号",
    }


def _make_llm_response(images: list | None = None) -> dict:
    """创建 LLM 响应。"""
    resp = {
        "category": "LLM",
        "sub_topic": "",
        "summary": "测试摘要" * 10,
        "key_points": ["**要点1**：核心观点"],
        "concepts": [{"name": "概念A", "description": "概念A是测试概念"}],
        "tags": ["测试"],
        "related_topics": [],
        "body_sections": [
            {"heading": "一、背景介绍", "content": "正文内容。" * 20},
            {"heading": "二、核心拆解", "content": "详细分析。" * 20},
        ],
    }
    if images is not None:
        resp["images"] = images
    return resp


def _process(article: dict, config: dict, vault_path: Path, articles_dir: Path) -> dict:
    """调用单篇处理编排，返回结果 dict。"""
    return _process_single(
        article,
        config,
        vault_path,
        articles_dir,
        [],
        [],
        ArchiveWriter(),
    )


# ---------------------------------------------------------------------------
# 集成测试
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    """完整 pipeline 集成测试（mock API）。"""

    @patch("wx_obsidian.orchestrator.summarize_article")
    @patch("wx_obsidian.orchestrator.describe_images")
    @patch("wx_obsidian.orchestrator.load_vision_config")
    @patch("wx_obsidian.processing.llm.fix_format_issues", return_value=None)
    def test_full_pipeline_with_vision(
        self,
        mock_fix_format: MagicMock,
        mock_vision_config: MagicMock,
        mock_describe: MagicMock,
        mock_summarize: MagicMock,
        tmp_path: Path,
        sample_config: dict,
    ):
        """完整 pipeline：Vision + LLM + Markdown 生成 + 写盘。"""
        articles_dir = tmp_path / "公众号文章"
        articles_dir.mkdir()

        # Mock APIs
        mock_vision_config.return_value = {
            "api_key": "test",
            "base_url": "http://test",
            "model": "test",
        }
        mock_describe.return_value = [
            ImageDescription(
                url=IMG,
                description="流程图展示了文章的整体工作原理",
                is_content=True,
                type="diagram",
                status="ok",
            )
        ]
        mock_summarize.return_value = _make_llm_response(
            images=[
                {
                    "url": IMG,
                    "placement": "一、背景介绍",
                    "purpose": "直观展示流程",
                    "valuable": True,
                }
            ]
        )

        article = _make_article(
            content_html="<p>这是一段很长的文章内容。" * 50
            + f'<img data-src="{IMG}">'
            + "<p>更多内容。</p>" * 20
        )

        result = _process(article, sample_config, tmp_path, articles_dir)

        assert result["article_id"] == "test_001"
        assert result["status"] == "done"
        assert mock_describe.called
        # 笔记已落盘，且 LLM 选中的图片出现在正文中
        written = Path(result["file"]).read_text(encoding="utf-8")
        assert IMG in written

    @patch("wx_obsidian.orchestrator.summarize_article")
    @patch("wx_obsidian.orchestrator.load_vision_config")
    @patch("wx_obsidian.processing.llm.fix_format_issues", return_value=None)
    def test_pipeline_no_images(
        self,
        mock_fix_format: MagicMock,
        mock_vision_config: MagicMock,
        mock_summarize: MagicMock,
        tmp_path: Path,
        sample_config: dict,
    ):
        """纯文本文章（无图片）走原路径。"""
        articles_dir = tmp_path / "公众号文章"
        articles_dir.mkdir()

        mock_vision_config.return_value = None
        mock_summarize.return_value = _make_llm_response()

        article = _make_article(content_html="<p>纯文本内容。</p>" * 50)
        result = _process(article, sample_config, tmp_path, articles_dir)

        assert result["article_id"] == "test_001"
        assert result["status"] == "done"

    @patch("wx_obsidian.orchestrator.summarize_article")
    @patch("wx_obsidian.orchestrator.describe_images")
    @patch("wx_obsidian.orchestrator.load_vision_config")
    @patch("wx_obsidian.processing.llm.fix_format_issues", return_value=None)
    def test_pipeline_vision_failure_degradation(
        self,
        mock_fix_format: MagicMock,
        mock_vision_config: MagicMock,
        mock_describe: MagicMock,
        mock_summarize: MagicMock,
        tmp_path: Path,
        sample_config: dict,
    ):
        """Vision 请求失败时降级到纯文本路径，处理仍能完成。"""
        articles_dir = tmp_path / "公众号文章"
        articles_dir.mkdir()

        mock_vision_config.return_value = {
            "api_key": "test",
            "base_url": "http://test",
            "model": "test",
        }
        # 真实网络故障会抛 requests.RequestException，由 _vision_stage 捕获后降级
        mock_describe.side_effect = requests.RequestException("API 连接失败")
        mock_summarize.return_value = _make_llm_response()

        article = _make_article(content_html="<p>内容。</p>" * 50 + f'<img data-src="{IMG}">')
        result = _process(article, sample_config, tmp_path, articles_dir)

        assert result["article_id"] == "test_001"
        assert result["status"] == "done"
        # 降级为纯文本：不向 LLM 传图片描述
        assert mock_summarize.call_args.kwargs["image_descriptions"] is None

    def test_pipeline_content_too_short(self, tmp_path: Path, sample_config: dict):
        """内容过短时跳过处理。"""
        articles_dir = tmp_path / "公众号文章"
        articles_dir.mkdir()

        # 不提供 URL，避免触发抓取
        article = _make_article(content_html="<p>短</p>")
        article["url"] = ""
        result = _process(article, sample_config, tmp_path, articles_dir)

        assert result["article_id"] == "test_001"
        assert result["status"] == "skipped"
        assert result["reason"] == "no_content"


# ---------------------------------------------------------------------------
# 回归测试
# ---------------------------------------------------------------------------


class TestRegression:
    """回归测试：确保原有行为不变。"""

    @patch("wx_obsidian.orchestrator.summarize_article")
    @patch("wx_obsidian.orchestrator.load_vision_config")
    @patch("wx_obsidian.processing.llm.fix_format_issues", return_value=None)
    def test_keyword_matching_fallback(
        self,
        mock_fix_format: MagicMock,
        mock_vision_config: MagicMock,
        mock_summarize: MagicMock,
        tmp_path: Path,
        sample_config: dict,
    ):
        """未配 Vision 且 LLM 未返回 images 时，降级到关键词匹配插入。"""
        articles_dir = tmp_path / "公众号文章"
        articles_dir.mkdir()

        mock_vision_config.return_value = None
        # LLM 响应不含 images 字段
        mock_summarize.return_value = _make_llm_response()

        article = _make_article(
            content_html="<p>很长的文章内容。</p>" * 50 + f'<img data-src="{IMG}">'
        )

        with patch(
            "wx_obsidian.orchestrator.insert_images_into_markdown",
            wraps=insert_images_into_markdown,
        ) as mock_insert:
            result = _process(article, sample_config, tmp_path, articles_dir)

        assert result["status"] == "done"
        # 未配视觉时仍会尝试用关键词匹配把图片插入正文
        assert mock_insert.called
        passed_images = mock_insert.call_args.args[1]
        assert IMG in [im["url"] for im in passed_images]


# ---------------------------------------------------------------------------
# 边界条件
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """边界条件测试。"""

    def test_pipeline_runs_all_stages(self):
        """run_pipeline 顺序执行所有 stage；短路由各 stage 自查 __skip 实现。"""

        def skip_stage(ctx: PipelineContext) -> PipelineContext:
            ctx.processed["__skip"] = {"status": "skipped"}
            return ctx

        def later_stage(ctx: PipelineContext) -> PipelineContext:
            ctx.processed["ran"] = True
            return ctx

        ctx = PipelineContext(article={}, content="", images=[])
        ctx = run_pipeline(ctx, [skip_stage, later_stage])

        assert ctx.processed["__skip"] is not None
        # run_pipeline 自身不做短路，后续 stage 仍会被调用
        assert ctx.processed["ran"] is True
