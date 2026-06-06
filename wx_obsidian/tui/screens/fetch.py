"""抓取管理屏幕：手动抓取、进度显示、历史查看。"""

from __future__ import annotations

import asyncio
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, ProgressBar, Static


class FetchScreen(Screen[None]):
    """文章抓取管理。"""

    CSS = """
    #history-list {
        min-height: 10;
    }
    .history-item {
        padding: 0 2;
    }
    """

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    def __init__(self) -> None:
        super().__init__()
        self._fetch_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  文章抓取\n", id="page-title")
            yield Button("开始抓取", id="fetch-btn", variant="success")
            yield ProgressBar(id="fetch-progress")
            yield Static("", id="fetch-status")
            yield Static("\n  处理历史", id="history-label")
            yield ListView(id="history-list")
        yield Footer()

    def on_mount(self) -> None:
        self._fetch_task = asyncio.create_task(self._load_history())

    def on_unmount(self) -> None:
        if self._fetch_task and not self._fetch_task.done():
            self._fetch_task.cancel()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fetch-btn":
            self._fetch_task = asyncio.create_task(self._start_fetch())

    async def _load_history(self) -> None:
        """异步加载处理历史。"""
        list_view = self.query_one("#history-list", ListView)
        from wx_obsidian.config import load_processed

        processed = await asyncio.to_thread(load_processed)
        rows: list[str] = []
        for _aid, record in processed.items():
            if isinstance(record, dict) and record.get("status") == "done":
                title = record.get("title", "")[:40]
                category = record.get("category", "")
                rows.append(f"{title}  [{category}]")
                if len(rows) >= 100:
                    break
        await list_view.clear()
        for text in rows:
            await list_view.append(ListItem(Label(text, classes="history-item")))

    async def _start_fetch(self) -> None:
        """异步执行抓取。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        status = self.query_one("#fetch-status", Static)
        progress = self.query_one("#fetch-progress", ProgressBar)
        btn = self.query_one("#fetch-btn", Button)
        btn.disabled = True
        status.update("  正在抓取...")
        progress.progress = 0

        def on_progress(title: str, completed: int, total: int) -> None:
            if title == "_start":
                app.call_from_thread(progress.update, total=total)
            elif title == "_kg":
                app.call_from_thread(status.update, "  正在更新知识图谱...")
            elif title == "_kg_done":
                app.call_from_thread(progress.advance)
                app.call_from_thread(status.update, "  知识图谱更新完成")
            else:
                app.call_from_thread(progress.advance)
                short = title[:30] + ("..." if len(title) > 30 else "")
                app.call_from_thread(status.update, f"  [{completed}/{total}] {short}")

        try:
            results = await app.orchestrator.fetch_and_process(on_progress=on_progress)
            done = sum(1 for r in results if r.status == "done")
            status.update(f"  完成: {done}/{len(results)} 篇")
            await self._load_history()
        except Exception as e:
            status.update(f"  抓取失败: {e}")
        finally:
            btn.disabled = False
