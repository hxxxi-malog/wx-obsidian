"""定时任务屏幕：配置、启停、状态查看。"""

from __future__ import annotations

import asyncio
from typing import cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Select, Static


class SchedulerScreen(Screen[None]):
    """定时任务管理。"""

    CSS = """
    #jobs-list {
        min-height: 5;
    }
    .job-item {
        padding: 0 2;
    }
    """

    BINDINGS = [Binding("escape", "app.pop_screen", "返回")]

    _INTERVAL_OPTIONS = [
        ("每 1 小时", "1h"),
        ("每 2 小时", "2h"),
        ("每 6 小时", "6h"),
        ("每 12 小时", "12h"),
        ("每 24 小时", "24h"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield Static("\n  定时任务管理\n", id="page-title")

            yield Static("  抓取周期:", id="interval-label")
            yield Select(self._INTERVAL_OPTIONS, id="interval-select", allow_blank=False)

            yield Button("启动定时抓取", id="start-fetch-btn", variant="success")
            yield Button("停止定时抓取", id="stop-fetch-btn", variant="error")
            yield Button("立即执行一次", id="run-now-btn", variant="primary")

            yield Static("\n  保活任务 (每 7 天刷新微信读书 cookie)", id="keepalive-label")
            yield Button("启动保活", id="start-keepalive-btn", variant="success")
            yield Button("停止保活", id="stop-keepalive-btn", variant="error")

            yield Static("\n  任务状态", id="status-label")
            yield ListView(id="jobs-list")
        yield Footer()

    def on_mount(self) -> None:
        asyncio.create_task(self._refresh_jobs())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        scheduler = app.scheduler
        btn_id = event.button.id

        if btn_id == "start-fetch-btn":
            interval = self.query_one("#interval-select", Select).value
            hours = self._parse_interval(str(interval))
            if hours:

                async def _scheduled_fetch() -> None:
                    try:
                        results = await app.orchestrator.fetch_and_process()
                        done = sum(1 for r in results if r.status == "done")
                        app.show_notification(f"定时抓取完成: {done} 篇")
                    except Exception as e:
                        app.show_notification(f"定时抓取失败: {e}", severity="error")

                success = scheduler.add_job(
                    "fetch_job",
                    lambda: asyncio.ensure_future(_scheduled_fetch()),
                    trigger="interval",
                    hours=hours,
                )
                if success:
                    scheduler.start()
                    app.show_notification(f"已启动定时抓取 (每 {hours} 小时)")
                else:
                    app.show_notification("启动失败", severity="error")
            asyncio.create_task(self._refresh_jobs())

        elif btn_id == "stop-fetch-btn":
            scheduler.remove_job("fetch_job")
            app.show_notification("已停止定时抓取")
            asyncio.create_task(self._refresh_jobs())

        elif btn_id == "run-now-btn":
            app.show_notification("正在执行抓取...")
            asyncio.create_task(self._run_now(app))

        elif btn_id == "start-keepalive-btn":

            async def _scheduled_keepalive() -> None:
                try:
                    await self._keepalive(app)
                except Exception as e:
                    app.show_notification(f"保活任务失败: {e}", severity="error")

            success = scheduler.add_job(
                "keepalive_job",
                lambda: asyncio.ensure_future(_scheduled_keepalive()),
                trigger="interval",
                days=7,
            )
            if success:
                scheduler.start()
                app.show_notification("已启动保活任务 (每 7 天)")
            else:
                app.show_notification("启动失败", severity="error")
            asyncio.create_task(self._refresh_jobs())

        elif btn_id == "stop-keepalive-btn":
            scheduler.remove_job("keepalive_job")
            app.show_notification("已停止保活任务")
            asyncio.create_task(self._refresh_jobs())

    def _parse_interval(self, value: str) -> int | None:
        """解析间隔选项为小时数。"""
        mapping = {"1h": 1, "2h": 2, "6h": 6, "12h": 12, "24h": 24}
        return mapping.get(value)

    async def _refresh_jobs(self) -> None:
        """刷新任务列表。"""
        from wx_obsidian.tui.app import WxObsidianApp

        app = cast(WxObsidianApp, self.app)
        list_view = self.query_one("#jobs-list", ListView)
        jobs = app.scheduler.list_jobs()
        texts = [
            f"{job.job_id}  |  {job.cron}  |  {'运行中' if app.scheduler.get_job_status(job.job_id) else '已停止'}"
            for job in jobs
        ]
        await list_view.clear()
        for text in texts:
            await list_view.append(ListItem(Label(text, classes="job-item")))

    async def _run_now(self, app: object) -> None:
        """立即执行一次抓取。"""
        from wx_obsidian.tui.app import WxObsidianApp

        typed_app = cast(WxObsidianApp, app)
        try:
            results = await typed_app.orchestrator.fetch_and_process()
            done = sum(1 for r in results if r.status == "done")
            typed_app.show_notification(f"抓取完成: {done}/{len(results)} 篇")
            await self._refresh_jobs()
        except Exception as e:
            typed_app.show_notification(f"抓取失败: {e}", severity="error")

    async def _keepalive(self, app: object) -> None:
        """保活：刷新微信读书 cookie。"""
        from wx_obsidian.tui.app import WxObsidianApp

        typed_app = cast(WxObsidianApp, app)
        success = await asyncio.to_thread(typed_app.orchestrator.refresh_cookie)
        if not success:
            typed_app.show_notification("保活失败，请重新扫码登录", severity="error")
