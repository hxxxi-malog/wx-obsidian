"""主页：系统状态概览 + 功能入口卡片。"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

logger = logging.getLogger(__name__)

_SCREEN_TITLES: dict[str, str] = {
    "container": "容器管理",
    "account": "账号管理",
    "feeds": "公众号管理",
    "config": "配置管理",
    "fetch": "文章抓取",
    "scheduler": "定时任务",
}


class HomeScreen(Screen[None]):
    """主页：系统状态概览 + 功能入口。"""

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("1", "navigate('container')", "容器管理"),
        Binding("2", "navigate('account')", "账号管理"),
        Binding("3", "navigate('feeds')", "公众号管理"),
        Binding("4", "navigate('config')", "配置管理"),
        Binding("5", "navigate('fetch')", "文章抓取"),
        Binding("6", "navigate('scheduler')", "定时任务"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._refresh_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("wx-obsidian v1.0 — 公众号文章 → Obsidian 知识库", id="welcome")
            yield Static("", id="status-summary")
            yield Static(
                "\n  [1] 容器管理  [2] 账号管理  [3] 公众号管理\n"
                "  [4] 配置管理  [5] 文章抓取  [6] 定时任务\n"
                "\n  按数字键导航，按 q 退出",
                id="menu",
            )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_task = asyncio.create_task(self.refresh_status())

    def on_screen_resume(self) -> None:
        """从子屏幕返回时刷新状态。"""
        self._cancel_refresh()
        self._refresh_task = asyncio.create_task(self.refresh_status())

    def on_unmount(self) -> None:
        self._cancel_refresh()

    def _cancel_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()

    async def refresh_status(self) -> None:
        """异步刷新状态概览。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        try:
            health = await asyncio.to_thread(app.orchestrator.get_health_status)
            stats = await asyncio.to_thread(app.orchestrator.get_statistics)
            # 仅在 HomeScreen 是当前屏幕时更新 widget，避免后台更新导致其他屏幕滚动重置
            if not self.is_current:
                return
            status = self.query_one("#status-summary", Static)
            status.update(
                f"\n  状态: {'● 健康' if health.overall else '○ 异常'}\n"
                f"  已处理: {stats.processed_articles} 篇 | "
                f"失败: {stats.failed_articles} 篇\n"
            )
        except Exception:
            if not self.is_current:
                return
            logger.warning("首页状态刷新失败", exc_info=True)
            try:
                status = self.query_one("#status-summary", Static)
                status.update("\n  状态: ○ 检测失败（请检查配置和网络）\n")
            except Exception:
                pass

    def action_navigate(self, screen_name: str) -> None:
        """导航到指定屏幕。"""
        from wx_obsidian.tui.app import WxObsidianApp

        cast(WxObsidianApp, self.app).push_screen(screen_name)
