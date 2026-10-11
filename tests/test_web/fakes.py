"""P1 测试辅助：可控的 fake orchestrator（可触发哨兵进度、可取消）。"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

from wx_obsidian.models import ProcessingResult
from wx_obsidian.web.runtime import WebRuntime


class FakeOrchestrator:
    """可编程的 Orchestrator 替身。

    `steps` 是逐篇「标题」，每篇处理时会调用一次 on_progress（模拟真实哨兵协议），
    并在处理前检查 cancel —— 与 orchestrator 的真实语义一致。
    """

    def __init__(
        self,
        *,
        healthy: bool = True,
        steps: list[str] | None = None,
        script: list[tuple[str, int, int]] | None = None,
        fail_with: Exception | None = None,
        step_delay: float = 0.02,
    ) -> None:
        self.healthy = healthy
        self.steps = steps if steps is not None else ["第一篇", "第二篇", "第三篇"]
        self.script = script
        self.fail_with = fail_with
        self.step_delay = step_delay
        self.cancelled = threading.Event()
        self.calls: list[dict[str, Any]] = []
        self.opened: list[str] = []

    # -- 健康与统计（P0 已有测试依赖） --------------------------------------

    def get_health_status(self):
        from wx_obsidian.models import ConnectionTestResult, HealthStatus

        result = ConnectionTestResult(
            success=self.healthy, latency_ms=12.5, message="ok" if self.healthy else "连接失败"
        )
        return HealthStatus(
            source=result,
            llm_api=result,
            vision_api=result,
            vault_path=ConnectionTestResult(success=True, latency_ms=0, message="Vault 路径存在"),
        )

    def get_statistics(self):
        from wx_obsidian.models import Statistics

        return Statistics(
            total_articles=5,
            processed_articles=4,
            failed_articles=1,
            categories={"LLM": 3, "Agent架构": 1},
        )

    def get_failed_articles(self) -> list[Any]:
        return []

    # -- 任务 ---------------------------------------------------------------

    async def fetch_and_process(
        self,
        limit: int = 0,
        on_progress=None,
        force: bool = False,
        article_id: str = "",
        cancel: threading.Event | None = None,
    ) -> list[ProcessingResult]:
        self.calls.append(
            {"kind": "fetch", "limit": limit, "force": force, "article_id": article_id}
        )
        if self.fail_with is not None:
            raise self.fail_with

        if self.script is not None:
            return await self._run_script(on_progress)

        steps = self.steps[:limit] if limit else list(self.steps)
        if on_progress:
            on_progress("_start", 0, len(steps))

        results: list[ProcessingResult] = []
        for index, title in enumerate(steps, start=1):
            if cancel is not None and cancel.is_set():
                self.cancelled.set()
                break
            await asyncio.sleep(self.step_delay)
            if on_progress:
                on_progress(title, index, len(steps))
            results.append(
                ProcessingResult(
                    article_id=f"id-{index}",
                    title=title,
                    status="done",
                    category="LLM",
                    file_path=f"/tmp/{index}.md",
                )
            )
        if on_progress:
            on_progress("_done", len(results), len(steps))
        return results

    async def retry_failed(self, article_ids: list[str]) -> list[ProcessingResult]:
        self.calls.append({"kind": "retry", "article_ids": list(article_ids)})
        return [
            ProcessingResult(article_id=aid, title=f"重试 {aid}", status="done", category="LLM")
            for aid in article_ids
        ]

    async def _run_script(self, on_progress) -> list[ProcessingResult]:
        """按预设脚本原样回放哨兵，用于断言事件类型映射。"""
        script = self.script or []
        total = sum(1 for title, _, _ in script if not title.startswith("_"))
        if on_progress:
            on_progress("_start", 0, total)
        for title, completed, tot in script:
            await asyncio.sleep(0)
            if on_progress:
                on_progress(title, completed, tot)
        if on_progress:
            on_progress("_done", total, total)
        return [
            ProcessingResult(article_id=f"id-{i}", title=f"文章{i}", status="done", category="LLM")
            for i in range(1, total + 1)
        ]


class FakeRuntime:
    """WebRuntime 替身。"""

    def __init__(self, orchestrator: FakeOrchestrator, config_manager: Any) -> None:
        self.orchestrator = orchestrator
        self.config_manager = config_manager
        self.scheduler = None


def make_runtime(
    *, orchestrator: FakeOrchestrator | None = None, config_manager: Any = None
) -> tuple[WebRuntime, FakeOrchestrator]:
    """返回 (WebRuntime, FakeOrchestrator)。"""
    fake = orchestrator or FakeOrchestrator()
    runtime = FakeRuntime(fake, config_manager)
    return cast(WebRuntime, cast(Any, runtime)), fake
