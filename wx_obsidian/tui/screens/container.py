"""容器管理屏幕：WeWe RSS 容器状态查看。"""

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
    """WeWe RSS 容器状态管理。"""

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._check_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  WeWe RSS 容器管理\n", id="page-title")
            yield StatusIndicator("WeWe RSS", id="wewe-status")
            yield Static(
                "\n  注意: V1 仅支持 HTTP 探活，请确保容器已通过 docker-compose 启动\n", id="hint"
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
        """异步检查容器状态。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        try:
            healthy = await asyncio.to_thread(app.orchestrator.is_wewe_healthy)
            if not self.is_current:
                return
            self.query_one("#wewe-status", StatusIndicator).set_result(
                "WeWe RSS", healthy, "运行中" if healthy else "未运行"
            )
        except Exception:
            if not self.is_current:
                return
            self.query_one("#wewe-status", StatusIndicator).set_result(
                "WeWe RSS", False, "检测失败"
            )
