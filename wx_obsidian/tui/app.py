"""TUI 主应用：统一管理入口。"""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from typing import Literal

from textual.app import App
from textual.screen import Screen

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.orchestrator import Orchestrator
from wx_obsidian.scheduler import TaskScheduler
from wx_obsidian.tui.screens.account import AccountScreen
from wx_obsidian.tui.screens.articles import ArticlesScreen
from wx_obsidian.tui.screens.config import ConfigScreen
from wx_obsidian.tui.screens.container import ContainerScreen
from wx_obsidian.tui.screens.feeds import FeedsScreen
from wx_obsidian.tui.screens.fetch import FetchScreen
from wx_obsidian.tui.screens.home import HomeScreen
from wx_obsidian.tui.screens.scheduler import SchedulerScreen
from wx_obsidian.wewe_rss import WeWeRSSClient

logger = logging.getLogger(__name__)

_SCREEN_MAP: dict[str, type[Screen[None]]] = {
    "container": ContainerScreen,
    "account": AccountScreen,
    "feeds": FeedsScreen,
    "config": ConfigScreen,
    "fetch": FetchScreen,
    "articles": ArticlesScreen,
    "scheduler": SchedulerScreen,
}


class WxObsidianApp(App[None]):
    """wx-obsidian TUI 主应用。"""

    TITLE = "wx-obsidian"
    CSS = """
    Screen {
        layout: vertical;
    }
    #welcome {
        text-align: center;
        padding: 1 2;
        text-style: bold;
    }
    #status-summary {
        padding: 0 2;
    }
    #menu {
        padding: 1 2;
    }
    #placeholder-content {
        padding: 2;
    }
    #container-status, #login-status {
        padding: 0 1;
    }
    """

    def __init__(self, config_manager: ConfigManager | None = None) -> None:
        super().__init__()
        self.config_manager = config_manager or ConfigManager()
        wewe_url = self.config_manager.get("wewe_rss.base_url", "http://localhost:4000")
        auth_code = self.config_manager.get("wewe_rss.auth_code", "")
        self._wewe_rss = WeWeRSSClient(wewe_url, auth_code)
        self.orchestrator = Orchestrator(self.config_manager, self._wewe_rss)
        self.scheduler = TaskScheduler()

    def on_mount(self) -> None:
        """注册屏幕并显示主页。"""
        self.install_screen(HomeScreen(), name="home")
        for screen_name, screen_cls in _SCREEN_MAP.items():
            self.install_screen(screen_cls(), name=screen_name)
        self.push_screen("home")

    def on_unmount(self) -> None:
        """停止调度器。"""
        self.scheduler.stop()

    def update_status_bar(self) -> None:
        """更新状态栏显示。"""
        home = self.get_screen("home")
        if isinstance(home, HomeScreen):
            asyncio.create_task(home.refresh_status())

    def show_notification(
        self,
        message: str,
        severity: Literal["information", "warning", "error"] = "information",
    ) -> None:
        """显示通知消息。"""
        self.notify(message, severity=severity)

    def show_login_url(self) -> None:
        """打开 WeWe RSS 登录页面。"""
        url = self.orchestrator.get_login_url()
        try:
            webbrowser.open(url)
            self.notify(f"已在浏览器中打开登录页面: {url}", severity="information")
        except Exception:
            self.notify(f"无法打开浏览器，请手动访问: {url}", severity="warning")


def main() -> None:
    """TUI 入口。"""
    app = WxObsidianApp()
    app.run()
