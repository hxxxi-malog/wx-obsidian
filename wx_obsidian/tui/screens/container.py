"""数据源状态屏幕：we-mp-rss 服务探活。"""

from __future__ import annotations

import asyncio
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from wx_obsidian.tui.widgets.status import StatusIndicator


class ContainerScreen(Screen[None]):
    """数据源服务状态管理。"""

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._check_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  数据源服务状态\n", id="page-title")
            yield StatusIndicator("数据源", id="source-status")
            yield Static(
                "\n  注意: 仅做 HTTP 探活，请确保数据源服务已启动\n"
                "  we-mp-rss 默认 http://127.0.0.1:8001\n",
                id="hint",
            )
            yield Button("刷新状态", id="refresh-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self._check_task = asyncio.create_task(self._check_status())

    def on_unmount(self) -> None:
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            self._check_task = asyncio.create_task(self._check_status())

    async def _check_status(self) -> None:
        """异步检查数据源状态。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        label = app.source_label
        try:
            healthy = await asyncio.to_thread(app.orchestrator.is_source_healthy)
            if not self.is_current:
                return
            self.query_one("#source-status", StatusIndicator).set_result(
                label, healthy, "运行中" if healthy else "未运行"
            )
        except Exception:
            if not self.is_current:
                return
            self.query_one("#source-status", StatusIndicator).set_result(label, False, "检测失败")
