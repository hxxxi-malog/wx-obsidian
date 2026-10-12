"""TUI 主应用：统一管理入口。"""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from typing import Literal

from textual.app import App
from textual.screen import Screen

from wx_obsidian.config import resolve_source_base_url
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.orchestrator import Orchestrator
from wx_obsidian.sources.factory import create_article_source, resolve_source_type
from wx_obsidian.tui.screens.account import AccountScreen
from wx_obsidian.tui.screens.articles import ArticlesScreen
from wx_obsidian.tui.screens.config import ConfigScreen
from wx_obsidian.tui.screens.container import ContainerScreen
from wx_obsidian.tui.screens.feeds import FeedsScreen
from wx_obsidian.tui.screens.fetch import FetchScreen
from wx_obsidian.tui.screens.home import HomeScreen

logger = logging.getLogger(__name__)

_SCREEN_MAP: dict[str, type[Screen[None]]] = {
    "container": ContainerScreen,
    "account": AccountScreen,
    "feeds": FeedsScreen,
    "config": ConfigScreen,
    "fetch": FetchScreen,
    "articles": ArticlesScreen,
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
        self.config_manager.load()
        self.source_type = resolve_source_type(self.config_manager.get("source.type", ""))
        self.source = create_article_source(
            self.source_type,
            base_url=resolve_source_base_url(
                str(self.config_manager.get("source.base_url", "") or "")
            ),
            timeout=int(self.config_manager.get("source.timeout", 15) or 15),
            token=self.config_manager.get("source.token", ""),
        )
        self.orchestrator = Orchestrator(self.config_manager, self.source)

    @property
    def source_label(self) -> str:
        """数据源的展示名（用于状态栏/健康检查文案）。"""
        return "we-mp-rss"

    def on_mount(self) -> None:
        """注册屏幕并显示主页。"""
        self.install_screen(HomeScreen(), name="home")
        for screen_name, screen_cls in _SCREEN_MAP.items():
            self.install_screen(screen_cls(), name=screen_name)
        self.push_screen("home")

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
        """打开数据源的登录/授权页面（we-mp-rss 为微信读书页）。"""
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
