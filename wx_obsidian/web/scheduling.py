"""定时任务：把 TUI 的两个任务搬到 Web 进程里，并让设定跨重启生效。

与 TUI 的差别（也是 Web 版的优势）：任务跑在服务进程里，浏览器关掉照常执行；
并且用户设定会写进 `config.json`，`.app` 重启后自动恢复。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Callable

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.web.jobs import JobManager
from wx_obsidian.web.runtime import WebRuntime

logger = logging.getLogger(__name__)

FETCH_JOB_ID = "fetch_job"
KEEPALIVE_JOB_ID = "keepalive_job"

# 与 TUI 的间隔选项保持一致
ALLOWED_INTERVAL_HOURS = (1, 2, 6, 12, 24)
KEEPALIVE_INTERVAL_DAYS = 7

CONFIG_FETCH_INTERVAL = "scheduler.fetch_interval"
CONFIG_KEEPALIVE_ENABLED = "scheduler.keepalive_enabled"


@dataclass
class ScheduleState:
    """用户设定的定时任务状态（可跨重启恢复）。"""

    fetch_hours: int = 0
    keepalive_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetch_hours": self.fetch_hours,
            "keepalive_enabled": self.keepalive_enabled,
            "allowed_hours": list(ALLOWED_INTERVAL_HOURS),
            "keepalive_days": KEEPALIVE_INTERVAL_DAYS,
        }


def read_state(config_manager: ConfigManager) -> ScheduleState:
    """从配置读出用户设定。"""
    try:
        hours = int(config_manager.get(CONFIG_FETCH_INTERVAL, 0) or 0)
    except (TypeError, ValueError):
        hours = 0
    if hours not in ALLOWED_INTERVAL_HOURS:
        hours = 0  # 配置被手改坏时视作未启用，不擅自跑
    return ScheduleState(
        fetch_hours=hours,
        keepalive_enabled=bool(config_manager.get(CONFIG_KEEPALIVE_ENABLED, False)),
    )


def _fetch_callable(jobs: JobManager) -> Callable[[], Awaitable[None]]:
    async def _scheduled_fetch() -> None:
        # 走 JobManager 而不是直接调 orchestrator：这样单飞、进度事件、页面可见性都一致
        if jobs.busy:
            logger.warning("定时抓取跳过：已有任务在运行")
            return
        await jobs.submit("fetch")

    return _scheduled_fetch


def _keepalive_callable(runtime: WebRuntime) -> Callable[[], Awaitable[None]]:
    async def _scheduled_keepalive() -> None:
        success = await asyncio.to_thread(runtime.orchestrator.refresh_cookie)
        if not success:
            logger.warning("保活失败，请重新扫码登录")

    return _scheduled_keepalive


def _add_fetch_job(runtime: WebRuntime, jobs: JobManager, hours: int) -> bool:
    return runtime.scheduler.add_job(
        FETCH_JOB_ID,
        _fetch_callable(jobs),
        trigger="interval",
        hours=hours,
    )


def _add_keepalive_job(runtime: WebRuntime, jobs: JobManager) -> bool:
    return runtime.scheduler.add_job(
        KEEPALIVE_JOB_ID,
        _keepalive_callable(runtime),
        trigger="interval",
        days=KEEPALIVE_INTERVAL_DAYS,
    )


def apply_state(
    runtime: WebRuntime,
    jobs: JobManager,
    *,
    fetch_hours: int | None = None,
    keepalive_enabled: bool | None = None,
    persist: bool = True,
) -> ScheduleState:
    """按给定设定增删任务，并（可选）写回配置。

    Args:
        runtime: 运行期依赖（含 scheduler）。
        jobs: 任务管理器，定时抓取通过它提交，保证单飞与进度可见。
        fetch_hours: 抓取间隔小时数；0 表示停止。None 表示不改动。
        keepalive_enabled: 是否启用保活；None 表示不改动。
        persist: 是否写回 config.json。

    Returns:
        应用后的设定。

    Raises:
        ValueError: 间隔不在允许的选项内。
    """
    state = read_state(runtime.config_manager)

    if fetch_hours is not None:
        if fetch_hours == 0:
            runtime.scheduler.remove_job(FETCH_JOB_ID)
        else:
            if fetch_hours not in ALLOWED_INTERVAL_HOURS:
                raise ValueError(
                    f"间隔需为 {'/'.join(str(h) for h in ALLOWED_INTERVAL_HOURS)} 小时之一"
                )
            if not _add_fetch_job(runtime, jobs, fetch_hours):
                raise ValueError("添加定时抓取任务失败")
            runtime.scheduler.start()
        state.fetch_hours = fetch_hours
        if persist:
            runtime.config_manager.set(CONFIG_FETCH_INTERVAL, fetch_hours)

    if keepalive_enabled is not None:
        if keepalive_enabled:
            if not _add_keepalive_job(runtime, jobs):
                raise ValueError("添加保活任务失败")
            runtime.scheduler.start()
        else:
            runtime.scheduler.remove_job(KEEPALIVE_JOB_ID)
        state.keepalive_enabled = keepalive_enabled
        if persist:
            runtime.config_manager.set(CONFIG_KEEPALIVE_ENABLED, keepalive_enabled)

    if persist and (fetch_hours is not None or keepalive_enabled is not None):
        runtime.config_manager.save()

    return state


def restore_state(runtime: WebRuntime, jobs: JobManager) -> ScheduleState:
    """服务启动时按配置恢复定时任务。"""
    state = read_state(runtime.config_manager)
    if state.fetch_hours:
        if _add_fetch_job(runtime, jobs, state.fetch_hours):
            runtime.scheduler.start()
            logger.info("已恢复定时抓取：每 %d 小时", state.fetch_hours)
        else:
            logger.warning("恢复定时抓取失败（每 %d 小时）", state.fetch_hours)
    if state.keepalive_enabled:
        if _add_keepalive_job(runtime, jobs):
            runtime.scheduler.start()
            logger.info("已恢复保活任务")
        else:
            logger.warning("恢复保活任务失败")
    return state


def job_list(runtime: WebRuntime) -> list[dict[str, Any]]:
    """列出调度器里的任务（含下次触发时间）。"""
    result: list[dict[str, Any]] = []
    for status in runtime.scheduler.list_jobs():
        result.append(
            {
                "job_id": status.job_id,
                "name": status.name,
                "trigger": status.cron,
                "is_running": status.is_running,
                "next_run": status.next_run.isoformat() if status.next_run else None,
                "last_run": status.last_run.isoformat() if status.last_run else None,
                "last_error": status.last_error,
                "error_count": status.error_count,
            }
        )
    return result
