"""P0 测试辅助（保持向后兼容：只关心 health/stats 的调用方用这里）。"""

from __future__ import annotations

from typing import Any, cast

from tests.test_web.fakes import FakeOrchestrator, make_runtime
from wx_obsidian.web.runtime import WebRuntime


def make_fake_runtime(healthy: bool = True, config_manager: Any = None) -> WebRuntime:
    """返回可交给 create_app 的 fake WebRuntime。"""
    runtime, _ = make_runtime(
        orchestrator=FakeOrchestrator(healthy=healthy), config_manager=config_manager
    )
    return cast(WebRuntime, runtime)
