"""Pipeline 抽象：函数组合式 stage 编排。"""

from __future__ import annotations

from typing import Callable

from wx_obsidian.processing.models import PipelineContext

StageFn = Callable[[PipelineContext], PipelineContext]


def run_pipeline(ctx: PipelineContext, stages: list[StageFn]) -> PipelineContext:
    """按顺序执行 stage 函数链，每步传递并返回 PipelineContext。"""
    for stage in stages:
        ctx = stage(ctx)
    return ctx
