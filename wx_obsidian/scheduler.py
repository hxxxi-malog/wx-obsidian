"""定时任务调度器：封装 APScheduler AsyncIOScheduler。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from wx_obsidian.models import JobStatus

logger = logging.getLogger(__name__)


class TaskScheduler:
    """定时任务调度器，集成到 Textual 的 asyncio 事件循环。"""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._started = False

    def add_job(
        self,
        job_id: str,
        func: Callable[..., Any],
        trigger: str,
        **trigger_args: Any,
    ) -> bool:
        """添加定时任务。

        Args:
            job_id: 任务唯一标识。
            func: 异步或同步回调函数。
            trigger: 触发器类型（'cron' 或 'interval'）。
            **trigger_args: 传递给触发器的参数。

        Returns:
            是否添加成功。
        """
        try:
            self._scheduler.add_job(
                func,
                trigger=trigger,
                id=job_id,
                replace_existing=True,
                max_instances=1,
                **trigger_args,
            )
            logger.info("添加定时任务: %s (trigger=%s)", job_id, trigger)
            return True
        except Exception:
            logger.warning("添加定时任务失败: %s", job_id, exc_info=True)
            return False

    def remove_job(self, job_id: str) -> bool:
        """移除定时任务。"""
        try:
            self._scheduler.remove_job(job_id)
            logger.info("移除定时任务: %s", job_id)
            return True
        except Exception:
            logger.warning("移除定时任务失败: %s", job_id, exc_info=True)
            return False

    def start(self) -> None:
        """启动调度器。"""
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("定时任务调度器已启动")

    def stop(self) -> None:
        """停止调度器。"""
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("定时任务调度器已停止")

    def get_job_status(self, job_id: str) -> JobStatus | None:
        """获取单个任务状态。"""
        job = self._scheduler.get_job(job_id)
        if job is None:
            return None
        return self._job_to_status(job)

    def list_jobs(self) -> list[JobStatus]:
        """列出所有任务。"""
        return [self._job_to_status(job) for job in self._scheduler.get_jobs()]

    def run_job_now(self, job_id: str) -> None:
        """立即触发一次任务执行。"""
        job = self._scheduler.get_job(job_id)
        if job is not None:
            job.modify(next_run_time=datetime.now(tz=timezone.utc))
            logger.info("手动触发任务: %s", job_id)

    def _job_to_status(self, job: Any) -> JobStatus:
        """将 APScheduler Job 转换为 JobStatus。"""
        trigger_str = str(job.trigger)
        return JobStatus(
            job_id=job.id,
            name=job.name or job.id,
            cron=trigger_str,
            is_running=False,
            last_run=None,  # APScheduler 3.x 不直接暴露 last_run_time
            next_run=job.next_run_time,
        )
