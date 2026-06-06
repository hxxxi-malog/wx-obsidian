"""文章管理屏幕：查看已处理文章、级联删除。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static


class ConfirmDeleteScreen(ModalScreen[bool]):
    """删除确认对话框。"""

    CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }
    #confirm-dialog {
        grid-size: 2;
        grid-gutter: 1 2;
        grid-rows: 1fr 3;
        padding: 0 2;
        width: 60;
        height: 11;
        border: thick $background 80%;
        background: $surface;
    }
    #question {
        column-span: 2;
        content-align: center middle;
        padding: 1;
    }
    """

    def __init__(self, title: str) -> None:
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        with Grid(id="confirm-dialog"):
            yield Label(f"确定删除「{self._title[:30]}」？\n（关联数据将一并清理）", id="question")
            yield Button("删除", variant="error", id="yes")
            yield Button("取消", variant="default", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class ArticlesScreen(Screen[None]):
    """文章管理：查看和删除已处理文章。"""

    CSS = """
    #articles-table {
        height: 1fr;
    }
    #articles-toolbar {
        height: 3;
        padding: 0 2;
    }
    #articles-status {
        padding: 0 2;
        height: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "返回"),
        Binding("d", "delete_article", "删除文章"),
        Binding("r", "refresh", "刷新"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("  选择文章后按 d 删除，r 刷新列表", id="articles-toolbar")
        yield DataTable(id="articles-table", cursor_type="row")
        yield Static("", id="articles-status")
        yield Footer()

    def on_mount(self) -> None:
        self._load_task = asyncio.create_task(self._load_articles())

    def on_unmount(self) -> None:
        if self._load_task and not self._load_task.done():
            self._load_task.cancel()

    async def _load_articles(self) -> None:
        """异步加载文章列表。"""
        from wx_obsidian.config import load_processed

        table = self.query_one("#articles-table", DataTable)
        table.clear(columns=True)

        table.add_columns("标题", "分类", "子主题", "日期", "状态")

        processed = await asyncio.to_thread(load_processed)
        self._processed = processed
        self._article_ids: list[str] = []

        done_records: list[tuple[str, dict[str, Any]]] = []
        other_records: list[tuple[str, dict[str, Any]]] = []

        for aid, record in processed.items():
            if not isinstance(record, dict):
                continue
            if record.get("status") == "done":
                done_records.append((aid, record))
            else:
                other_records.append((aid, record))

        # 已完成的文章按日期倒序
        done_records.sort(key=lambda x: x[1].get("date", ""), reverse=True)

        for aid, record in done_records + other_records:
            self._article_ids.append(aid)
            table.add_row(
                record.get("title", "")[:50],
                record.get("category", ""),
                record.get("sub_topic", ""),
                record.get("date", "")[:10],
                record.get("status", ""),
            )

        status = self.query_one("#articles-status", Static)
        status.update(f"  共 {len(self._article_ids)} 篇文章")

    def action_refresh(self) -> None:
        """刷新文章列表。"""
        self._load_task = asyncio.create_task(self._load_articles())

    def action_delete_article(self) -> None:
        """删除选中的文章。"""
        table = self.query_one("#articles-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._article_ids):
            self.app.notify("请先选择一篇文章", severity="warning")
            return

        idx = table.cursor_row
        article_id = self._article_ids[idx]
        record = self._processed.get(article_id)
        if not isinstance(record, dict):
            return

        title = record.get("title", "未知")

        def on_confirm(delete: bool | None) -> None:
            if delete:
                asyncio.create_task(self._do_delete(article_id))

        self.app.push_screen(ConfirmDeleteScreen(title), on_confirm)

    async def _do_delete(self, article_id: str) -> None:
        """执行级联删除。"""
        from wx_obsidian.config import load_processed
        from wx_obsidian.config_manager import ConfigManager
        from wx_obsidian.output.cleanup import cascade_delete

        config_manager = ConfigManager()
        config_manager.load()

        vault_path_str = config_manager.get("obsidian.vault_path", "")
        if not vault_path_str:
            self.app.notify("未配置 obsidian.vault_path", severity="error")
            return

        vault_path = Path(vault_path_str)
        articles_dir_name = config_manager.get("obsidian.articles_dir", "公众号文章")
        articles_dir = vault_path / articles_dir_name

        processed = await asyncio.to_thread(load_processed)
        title = ""
        record = processed.get(article_id)
        if isinstance(record, dict):
            title = record.get("title", "")

        actions = await asyncio.to_thread(
            cascade_delete, vault_path, articles_dir, processed, article_id
        )

        self.app.notify(f"已删除「{title[:30]}」（{len(actions)} 项清理）", severity="information")

        # 刷新列表
        await self._load_articles()
