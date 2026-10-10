"""processing/models.py 测试：数据结构。"""

from __future__ import annotations

import pytest

from wx_obsidian.processing.models import ImageDescription, PipelineContext


class TestImageDescription:
    """ImageDescription dataclass 测试。"""

    def test_create_with_all_fields(self):
        """完整字段创建。"""
        desc = ImageDescription(
            url="https://mmbiz.qpic.cn/test.png",
            description="流程图",
            is_content=True,
            type="diagram",
            status="ok",
        )
        assert desc.url == "https://mmbiz.qpic.cn/test.png"
        assert desc.description == "流程图"
        assert desc.is_content is True
        assert desc.type == "diagram"
        assert desc.status == "ok"

    def test_frozen(self):
        """frozen=True，不可修改字段。"""
        desc = ImageDescription(
            url="https://test.png", description="", is_content=False, type="ad", status="ok"
        )
        with pytest.raises(AttributeError):
            desc.url = "https://other.png"  # type: ignore[misc]

    def test_equality(self):
        """相同字段值相等。"""
        d1 = ImageDescription(url="a", description="b", is_content=True, type="photo", status="ok")
        d2 = ImageDescription(url="a", description="b", is_content=True, type="photo", status="ok")
        assert d1 == d2


class TestPipelineContext:
    """PipelineContext dataclass 测试。"""

    def test_minimal_creation(self):
        """最小创建（只提供必填字段）。"""
        ctx = PipelineContext(article={}, content="", images=[])
        assert ctx.article == {}
        assert ctx.content == ""
        assert ctx.images == []
        assert ctx.image_descriptions is None
        assert ctx.summary_data is None
        assert ctx.md_content is None
        assert ctx.config == {}
        assert ctx.processed == {}

    def test_full_creation(self):
        """完整字段创建。"""
        ctx = PipelineContext(
            article={"id": "1", "title": "test"},
            content="正文",
            images=[{"url": "https://img.png", "before": "前", "after": "后"}],
            image_descriptions=[],
            summary_data={"category": "LLM"},
            md_content="# 标题",
            config={"key": "value"},
            processed={"step": 1},
        )
        assert ctx.article["title"] == "test"
        assert len(ctx.images) == 1
        assert ctx.md_content == "# 标题"

    def test_default_factory_independence(self):
        """默认 dict 工厂返回独立实例。"""
        ctx1 = PipelineContext(article={}, content="", images=[])
        ctx2 = PipelineContext(article={}, content="", images=[])
        ctx1.processed["key"] = "value"
        assert "key" not in ctx2.processed
