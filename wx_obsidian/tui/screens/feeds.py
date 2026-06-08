"""公众号管理屏幕：查看、添加、删除公众号。"""

from __future__ import annotations

import asyncio
import logging
from typing import cast

import requests as http_requests
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

logger = logging.getLogger(__name__)


class FeedsScreen(Screen[None]):
    """公众号管理。"""

    CSS = """
    #feeds-list {
        height: 1fr;
    }
    #add-section {
        height: auto;
    }
    .feed-item {
        padding: 0 2;
    }
    """

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("\n  公众号管理\n", id="page-title")
        yield ListView(id="feeds-list")
        with Vertical(id="add-section"):
            yield Static("\n  输入文章链接添加公众号:", id="add-label")
            yield Input(placeholder="https://mp.weixin.qq.com/...", id="add-input")
            yield Button("添加", id="add-btn", variant="success")
            yield Button("刷新列表", id="refresh-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        asyncio.create_task(self._load_feeds())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "refresh-btn":
            asyncio.create_task(self._load_feeds())
        elif event.button.id == "add-btn":
            asyncio.create_task(self._add_feed())

    async def _load_feeds(self) -> None:
        """异步加载公众号列表。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        list_view = self.query_one("#feeds-list", ListView)
        try:
            feeds = await asyncio.to_thread(app.orchestrator.get_feeds)
            await list_view.clear()
            for feed in feeds:
                item = ListItem(Label(f"{feed.name}  —  {feed.intro[:50]}", classes="feed-item"))
                await list_view.append(item)
        except Exception as e:
            logger.warning("加载公众号列表失败: %s", e)
            app.show_notification(f"加载公众号列表失败: {e}", severity="error")

    async def _add_feed(self) -> None:
        """异步添加公众号。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        input_widget = self.query_one("#add-input", Input)
        url = input_widget.value.strip()
        if not url:
            app.show_notification("请输入文章链接", severity="warning")
            return
        try:
            result = await asyncio.to_thread(app.orchestrator.add_feed, url)
            if result:
                app.show_notification(f"已添加: {result.name}", severity="information")
                input_widget.value = ""
                await self._load_feeds()
            else:
                app.show_notification(
                    "添加失败: 链接无效或无法识别公众号，请确认是微信公众号文章链接",
                    severity="error",
                )
        except http_requests.ConnectionError:
            logger.warning("添加公众号失败: 无法连接 WeWe RSS 服务")
            app.show_notification(
                "添加失败: 无法连接 WeWe RSS 服务，请检查服务是否运行",
                severity="error",
            )
        except http_requests.RequestException as e:
            logger.warning("添加公众号失败: HTTP 请求错误 %s", e)
            app.show_notification(f"添加失败: 网络错误 {e}", severity="error")
        except Exception as e:
            logger.warning("添加公众号失败: %s", e)
            app.show_notification(f"添加失败: {e}", severity="error")
