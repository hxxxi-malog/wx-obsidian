"""P1 测试辅助：可控的 fake orchestrator（可触发哨兵进度、可取消）。"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from typing import Any, cast

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.models import (
    AccountStatus,
    ConnectionTestResult,
    Feed,
    HealthStatus,
    ProcessingResult,
    Statistics,
)
from wx_obsidian.scheduler import TaskScheduler
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
        feeds: list[Feed] | None = None,
        add_feed_supported: bool = True,
        source_healthy: bool = True,
        account_logged_in: bool = True,
        account_queried: bool = True,
    ) -> None:
        self.healthy = healthy
        self.steps = steps if steps is not None else ["第一篇", "第二篇", "第三篇"]
        self.script = script
        self.fail_with = fail_with
        self.step_delay = step_delay
        self.feeds = feeds if feeds is not None else []
        self.add_feed_supported = add_feed_supported
        self.source_healthy = source_healthy
        self.account_logged_in = account_logged_in
        self.account_queried = account_queried
        self.cancelled = threading.Event()
        self.calls: list[dict[str, Any]] = []
        self.opened: list[str] = []
        self.refreshed = 0

    # -- 健康与统计（P0 已有测试依赖） --------------------------------------

    def get_health_status(self) -> HealthStatus:
        result = ConnectionTestResult(
            success=self.healthy, latency_ms=12.5, message="ok" if self.healthy else "连接失败"
        )
        return HealthStatus(
            source=result,
            llm_api=result,
            vision_api=result,
            vault_path=ConnectionTestResult(success=True, latency_ms=0, message="Vault 路径存在"),
        )

    def get_statistics(self) -> Statistics:
        return Statistics(
            total_articles=5,
            processed_articles=4,
            failed_articles=1,
            categories={"LLM": 3, "Agent架构": 1},
        )

    def get_failed_articles(self) -> list[Any]:
        return []

    # -- 数据源 / 公众号 / 账号（P2） ---------------------------------------

    def is_source_healthy(self) -> bool:
        return self.source_healthy

    def get_feeds(self) -> list[Feed]:
        return list(self.feeds)

    def add_feed(self, article_url: str) -> Feed | None:
        self.calls.append({"kind": "add_feed", "url": article_url})
        if not self.add_feed_supported:
            return None
        feed = Feed(id="feed-new", name="新订阅的公众号")
        self.feeds.append(feed)
        return feed

    def delete_feed(self, feed_id: str) -> bool:
        self.calls.append({"kind": "delete_feed", "feed_id": feed_id})
        before = len(self.feeds)
        self.feeds = [feed for feed in self.feeds if feed.id != feed_id]
        return len(self.feeds) < before

    def get_account_status(self) -> AccountStatus:
        return AccountStatus(
            is_logged_in=self.account_logged_in,
            username="测试账号" if self.account_logged_in else None,
            need_refresh=not self.account_logged_in,
            queried=self.account_queried,
        )

    def get_login_url(self) -> str:
        return "http://127.0.0.1:8001/login"

    def refresh_cookie(self) -> bool:
        self.refreshed += 1
        return True

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
    """WebRuntime 替身。

    `config_manager` / `scheduler` 必须给真货：应用 lifespan 会读配置恢复定时任务、
    关闭时停调度器，给 None 会直接崩（这正是测试该暴露的）。
    config_manager 默认指向独立临时目录，绝不碰用户真实的 ~/.wx-obsidian。
    """

    def __init__(
        self,
        orchestrator: FakeOrchestrator,
        config_manager: Any = None,
        scheduler: Any = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.config_manager = (
            config_manager if config_manager is not None else _temp_config_manager()
        )
        self.scheduler = scheduler if scheduler is not None else TaskScheduler()


def _temp_config_manager() -> ConfigManager:
    """构造一个读不到任何用户配置的 ConfigManager。"""
    cm = ConfigManager(config_dir=Path(tempfile.mkdtemp(prefix="wx-web-test-")))
    cm.load()
    return cm


def make_runtime(
    *,
    orchestrator: FakeOrchestrator | None = None,
    config_manager: Any = None,
    scheduler: Any = None,
) -> tuple[WebRuntime, FakeOrchestrator]:
    """返回 (WebRuntime, FakeOrchestrator)。"""
    fake = orchestrator or FakeOrchestrator()
    runtime = FakeRuntime(fake, config_manager, scheduler)
    return cast(WebRuntime, cast(Any, runtime)), fake
