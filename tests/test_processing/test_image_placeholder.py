"""图片占位符与输出 token 预算的回归测试。

背景（2026-10-10 调研）：某篇 4 图文章的笔记正文实际插入 0 张图。
根因不是模型不遵从提示词，而是**响应被 max_tokens 截断**——
`body_sections`（含 `[IMG:N]` 占位符）排在 JSON schema 末尾，截断后整段丢失。
两个修复各自需要回归保护：
1. llm.py 的输出预算下限兜底（推理型模型要预留 reasoning token）；
2. orchestrator._image_stage 在「选了图但无占位符」时降级为程序化插入。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from wx_obsidian.orchestrator import _image_stage
from wx_obsidian.processing.llm import DEFAULT_MIN_OUTPUT_TOKENS
from wx_obsidian.processing.models import ImageDescription, PipelineContext

IMG_A = "https://mmbiz.qpic.cn/a.png"
IMG_B = "https://mmbiz.qpic.cn/b.png"


def _ctx(md: str, summary: dict[str, Any] | None) -> PipelineContext:
    return PipelineContext(
        article={"id": "x"},
        content="正文",
        images=[
            {
                "url": IMG_A,
                "before": "前文语境足够长以便匹配章节内容。" * 3,
                "after": "后文语境足够长以便匹配章节内容。" * 3,
            },
            {
                "url": IMG_B,
                "before": "另一段前文语境足够长以便匹配。" * 3,
                "after": "另一段后文语境足够长以便匹配。" * 3,
            },
        ],
        image_descriptions=[
            ImageDescription(
                url=IMG_A, description="A 图", is_content=True, type="diagram", status="ok"
            ),
            ImageDescription(
                url=IMG_B, description="B 图", is_content=True, type="photo", status="ok"
            ),
        ],
        summary_data=summary,
        md_content=md,
    )


class TestImageStagePlaceholders:
    """占位符替换与降级插入。"""

    def test_replaces_placeholders(self) -> None:
        """正常路径：LLM 写了占位符，按索引替换。"""
        summary = {
            "images": [{"url": IMG_A, "purpose": "示意图A"}, {"url": IMG_B, "purpose": "示意图B"}]
        }
        md = "# 标题\n\n[IMG:1]\n\n中间段落\n\n[IMG:2]\n\n结尾段落"
        result = _image_stage(_ctx(md, summary))

        assert result.md_content is not None
        assert f"![示意图A]({IMG_A})" in result.md_content
        assert f"![示意图B]({IMG_B})" in result.md_content
        assert "[IMG:" not in result.md_content

    def test_falls_back_when_no_placeholder(self) -> None:
        """关键回归：LLM 选了图但正文没有占位符时，必须降级插入而不是静默丢图。"""
        summary = {"images": [{"url": IMG_A, "purpose": "示意图A"}]}
        md = "# 标题\n\n## 一、背景\n\n这是一段足够长的正文内容，用于让关键词匹配有素材可依据。\n\n## 二、结论\n\n结论段落同样需要足够的文字内容来承载图片匹配逻辑。"
        result = _image_stage(_ctx(md, summary))

        assert result.md_content is not None
        assert IMG_A in result.md_content, "降级插入未生效，图片被静默丢弃"

    def test_removes_leftover_placeholders(self) -> None:
        """LLM 引用了不存在的索引时，占位符应被清除而非留在笔记里。"""
        summary = {"images": [{"url": IMG_A, "purpose": "A"}]}
        md = "# 标题\n\n[IMG:1]\n\n正文\n\n[IMG:9]\n"
        result = _image_stage(_ctx(md, summary))

        assert result.md_content is not None
        assert "[IMG:" not in result.md_content

    def test_no_images_in_summary_uses_keyword_fallback(self) -> None:
        """LLM 未给 images（如未配视觉）时，走关键词匹配插入。"""
        md = "# 标题\n\n## 一、背景\n\n这是一段足够长的正文内容，用于验证关键词匹配插入路径。"
        result = _image_stage(_ctx(md, {"images": []}))

        assert result.md_content is not None
        assert not result.processed


class TestNoVisionImageFallback:
    """关掉视觉模型时同样不能丢图（走与 LLM 选图路径相同的兜底）。"""

    @staticmethod
    def _ctx_no_vision(md: str, images: list[dict[str, Any]]) -> PipelineContext:
        return PipelineContext(
            article={"id": "x"},
            content="正文",
            images=images,
            image_descriptions=None,
            summary_data={"images": []},
            md_content=md,
        )

    def test_appends_image_when_keyword_match_fails(self) -> None:
        """回归：视觉关闭时关键词匹配未命中，图片仍须落到笔记里。"""
        images = [{"url": IMG_A, "before": "zzz qqq vvv", "after": "www eee rrr"}]
        md = "# 标题\n\n## 一、背景\n\n完全无关的正文内容，不含任何可匹配的关键词。"
        result = _image_stage(self._ctx_no_vision(md, images))

        assert result.md_content is not None
        assert IMG_A in result.md_content, "视觉关闭且关键词未命中时图片被静默丢弃"

    def test_keeps_image_in_body_when_keyword_match_succeeds(self) -> None:
        """关键词能匹配时图片应插到章节里，而不是一路追加到文末。"""
        ctx_words = "Transformer 注意力机制 自注意力 多头"
        images = [{"url": IMG_A, "before": ctx_words, "after": ctx_words}]
        md = (
            "# 标题\n\n## 一、背景\n\n"
            "Transformer 注意力机制 自注意力 多头 的正文内容足够长以承载匹配。\n\n"
            "## 二、结论\n\n收尾段落。"
        )
        result = _image_stage(self._ctx_no_vision(md, images))

        assert result.md_content is not None
        assert IMG_A in result.md_content
        assert not result.md_content.rstrip().endswith(")")


class TestOutputTokenBudget:
    """输出预算必须给 reasoning 留足空间，否则 JSON 会从尾部被截断。"""

    @staticmethod
    def _capture_kwargs(prompt_len: int, llm_cfg: dict[str, Any]) -> dict[str, Any]:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{"message": {"content": '{"category":"x"}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 1},
        }
        with patch("wx_obsidian.processing.llm.requests.post", return_value=resp) as mock_post:
            from wx_obsidian.processing.llm import _call_llm

            _call_llm("s" * 10, "u" * (prompt_len - 10), config={"llm": llm_cfg})
        return dict(mock_post.call_args.kwargs["json"])

    def test_short_prompt_still_gets_reasoning_headroom(self) -> None:
        """回归：prompt_len≈20000 时旧规则只给 9977，会截断。现在必须不低于下限。"""
        sent = self._capture_kwargs(20000, {"api_key": "k", "max_tokens": 4096})
        assert sent["max_tokens"] >= DEFAULT_MIN_OUTPUT_TOKENS
        assert sent["max_tokens"] == 16384

    def test_long_prompt_uses_larger_budget(self) -> None:
        sent = self._capture_kwargs(100000, {"api_key": "k", "max_tokens": 4096})
        assert sent["max_tokens"] > DEFAULT_MIN_OUTPUT_TOKENS

    def test_ceiling_is_respected(self) -> None:
        sent = self._capture_kwargs(400000, {"api_key": "k", "max_tokens": 4096})
        assert sent["max_tokens"] == 32768

    def test_explicit_larger_budget_wins(self) -> None:
        sent = self._capture_kwargs(20000, {"api_key": "k", "max_tokens": 24000})
        assert sent["max_tokens"] == 24000
