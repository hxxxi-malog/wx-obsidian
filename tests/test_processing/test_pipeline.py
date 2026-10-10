"""processing/pipeline.py 测试：Pipeline 执行。"""

from __future__ import annotations

from wx_obsidian.processing.models import PipelineContext
from wx_obsidian.processing.pipeline import run_pipeline


def _make_ctx() -> PipelineContext:
    """创建测试用 PipelineContext。"""
    return PipelineContext(article={"id": "1"}, content="test", images=[])


class TestRunPipeline:
    """run_pipeline 测试。"""

    def test_empty_stages(self):
        """空 stages 列表返回原 context。"""
        ctx = _make_ctx()
        result = run_pipeline(ctx, [])
        assert result is ctx

    def test_single_stage(self):
        """单个 stage 正确执行。"""

        def add_key(ctx: PipelineContext) -> PipelineContext:
            ctx.processed["step1"] = True
            return ctx

        ctx = _make_ctx()
        result = run_pipeline(ctx, [add_key])
        assert result.processed["step1"] is True

    def test_sequential_execution(self):
        """多个 stage 按顺序执行。"""
        order: list[str] = []

        def stage_a(ctx: PipelineContext) -> PipelineContext:
            order.append("a")
            return ctx

        def stage_b(ctx: PipelineContext) -> PipelineContext:
            order.append("b")
            return ctx

        def stage_c(ctx: PipelineContext) -> PipelineContext:
            order.append("c")
            return ctx

        ctx = _make_ctx()
        run_pipeline(ctx, [stage_a, stage_b, stage_c])
        assert order == ["a", "b", "c"]

    def test_context_propagation(self):
        """每个 stage 能读取前一个 stage 的修改。"""

        def set_content(ctx: PipelineContext) -> PipelineContext:
            ctx.content = "modified"
            return ctx

        def check_content(ctx: PipelineContext) -> PipelineContext:
            ctx.processed["saw"] = ctx.content
            return ctx

        ctx = _make_ctx()
        result = run_pipeline(ctx, [set_content, check_content])
        assert result.processed["saw"] == "modified"

    def test_stage_can_short_circuit(self):
        """stage 可以设置 skip 标记，后续 stage 可检查。"""

        def skipper(ctx: PipelineContext) -> PipelineContext:
            ctx.processed["__skip"] = True
            return ctx

        def checker(ctx: PipelineContext) -> PipelineContext:
            if ctx.processed.get("__skip"):
                ctx.processed["skipped"] = True
            else:
                ctx.processed["skipped"] = False
            return ctx

        ctx = _make_ctx()
        result = run_pipeline(ctx, [skipper, checker])
        assert result.processed["skipped"] is True

    def test_returns_last_context(self):
        """返回最后一个 stage 的 context。"""

        def modify(ctx: PipelineContext) -> PipelineContext:
            ctx.content = "final"
            return ctx

        ctx = _make_ctx()
        result = run_pipeline(ctx, [modify])
        assert result.content == "final"
