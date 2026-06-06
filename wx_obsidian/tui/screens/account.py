"""账号管理屏幕：微信登录状态查看、扫码登录引导。"""

from __future__ import annotations

import asyncio
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Static

from wx_obsidian.tui.widgets.status import StatusIndicator


class AccountScreen(Screen[None]):
    """微信读书登录状态管理。"""

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._check_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  账号管理\n", id="page-title")
            yield StatusIndicator("微信读书", id="login-status")
            yield Static("", id="account-info")
            yield Button("刷新状态", id="refresh-btn", variant="primary")
            yield Button("打开登录页面", id="login-btn", variant="success")
        yield Footer()

    def on_mount(self) -> None:
        self._check_task = asyncio.create_task(self._check_status())

    def on_unmount(self) -> None:
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            self._check_task = asyncio.create_task(self._check_status())
        elif event.button.id == "login-btn":
            from wx_obsidian.tui.app import WxObsidianApp

            cast(WxObsidianApp, self.app).show_login_url()

    async def _check_status(self) -> None:
        """异步检查登录状态。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        try:
            account = await asyncio.to_thread(app.orchestrator.get_account_status)
        except Exception:
            if not self.is_current:
                return
            self.query_one("#login-status", StatusIndicator).set_result(
                "微信读书", False, "检测失败"
            )
            self.query_one("#account-info", Static).update(
                "\n  无法连接 WeWe RSS，请检查容器是否运行\n"
            )
            return

        if not self.is_current:
            return
        if account.is_logged_in:
            self.query_one("#login-status", StatusIndicator).set_result(
                "微信读书", True, f"已登录 ({account.username or '未知'})"
            )
            self.query_one("#account-info", Static).update("")
        else:
            self.query_one("#login-status", StatusIndicator).set_result("微信读书", False, "未登录")
            self.query_one("#account-info", Static).update("\n  请点击下方按钮在浏览器中扫码登录\n")
