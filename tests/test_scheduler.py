"""scheduler.py 单元测试。"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from wx_obsidian.scheduler import TaskScheduler


@pytest.fixture
def scheduler_parts() -> Generator[tuple[TaskScheduler, MagicMock], None, None]:
    """返回 (TaskScheduler, mock_async_scheduler) 对。"""
    with patch("wx_obsidian.scheduler.AsyncIOScheduler") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        s = TaskScheduler()
        yield s, mock_instance


# ---------------------------------------------------------------------------
# add_job
# ---------------------------------------------------------------------------


class TestAddJob:
    """add_job 测试。"""

    def test_add_interval_job(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """成功添加 interval 任务。"""
        scheduler, mock_sched = scheduler_parts
        mock_func = MagicMock()
        result = scheduler.add_job("fetch", mock_func, trigger="interval", hours=2)
        assert result is True
        mock_sched.add_job.assert_called_once_with(
            mock_func,
            trigger="interval",
            id="fetch",
            replace_existing=True,
            max_instances=1,
            hours=2,
        )

    def test_add_cron_job(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """成功添加 cron 任务。"""
        scheduler, mock_sched = scheduler_parts
        mock_func = MagicMock()
        result = scheduler.add_job("cron_job", mock_func, trigger="cron", hour="*/6")
        assert result is True
        mock_sched.add_job.assert_called_once_with(
            mock_func,
            trigger="cron",
            id="cron_job",
            replace_existing=True,
            max_instances=1,
            hour="*/6",
        )

    def test_add_job_failure(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """添加失败时返回 False。"""
        scheduler, mock_sched = scheduler_parts
        mock_sched.add_job.side_effect = ValueError("bad trigger")
        result = scheduler.add_job("bad", lambda: None, trigger="invalid")
        assert result is False

    def test_add_job_replace_existing(
        self, scheduler_parts: tuple[TaskScheduler, MagicMock]
    ) -> None:
        """重复添加同 ID 任务时替换。"""
        scheduler, mock_sched = scheduler_parts
        scheduler.add_job("dup", lambda: None, trigger="interval", hours=1)
        scheduler.add_job("dup", lambda: None, trigger="interval", hours=2)
        assert mock_sched.add_job.call_count == 2


# ---------------------------------------------------------------------------
# remove_job
# ---------------------------------------------------------------------------


class TestRemoveJob:
    """remove_job 测试。"""

    def test_remove_existing(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        result = scheduler.remove_job("existing_job")
        assert result is True
        mock_sched.remove_job.assert_called_once_with("existing_job")

    def test_remove_nonexistent(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """移除不存在的任务返回 False。"""
        scheduler, mock_sched = scheduler_parts
        from apscheduler.jobstores.base import JobLookupError

        mock_sched.remove_job.side_effect = JobLookupError("not found")
        result = scheduler.remove_job("ghost")
        assert result is False


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStartStop:
    """start/stop 幂等性测试。"""

    def test_start(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        scheduler.start()
        mock_sched.start.assert_called_once()
        assert scheduler._started is True

    def test_start_idempotent(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """重复 start 只调用一次。"""
        scheduler, mock_sched = scheduler_parts
        scheduler.start()
        scheduler.start()
        mock_sched.start.assert_called_once()

    def test_stop(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        scheduler.start()
        scheduler.stop()
        mock_sched.shutdown.assert_called_once_with(wait=False)
        assert scheduler._started is False

    def test_stop_without_start(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """未 start 时调用 stop 不报错。"""
        scheduler, mock_sched = scheduler_parts
        scheduler.stop()
        mock_sched.shutdown.assert_not_called()

    def test_stop_noop_when_not_started(
        self, scheduler_parts: tuple[TaskScheduler, MagicMock]
    ) -> None:
        """未启动时 stop 是 no-op。"""
        scheduler, _mock = scheduler_parts
        scheduler.stop()
        assert scheduler._started is False


# ---------------------------------------------------------------------------
# list_jobs / get_job_status
# ---------------------------------------------------------------------------


class TestJobStatus:
    """任务状态查询测试。"""

    def test_list_jobs_empty(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        mock_sched.get_jobs.return_value = []
        assert scheduler.list_jobs() == []

    def test_list_jobs(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        mock_job = MagicMock()
        mock_job.id = "job1"
        mock_job.name = "Job One"
        mock_job.trigger = MagicMock()
        mock_job.trigger.__str__ = lambda self: "interval[2:00:00]"
        mock_job.next_run_time = datetime(2026, 1, 1, 12, 0)
        mock_sched.get_jobs.return_value = [mock_job]

        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "job1"
        assert jobs[0].name == "Job One"
        assert jobs[0].next_run == datetime(2026, 1, 1, 12, 0)

    def test_get_job_status_existing(
        self, scheduler_parts: tuple[TaskScheduler, MagicMock]
    ) -> None:
        scheduler, mock_sched = scheduler_parts
        mock_job = MagicMock()
        mock_job.id = "job1"
        mock_job.name = "Job One"
        mock_job.trigger = MagicMock()
        mock_job.trigger.__str__ = lambda self: "cron[hour=*/6]"
        mock_job.next_run_time = None
        mock_sched.get_job.return_value = mock_job

        status = scheduler.get_job_status("job1")
        assert status is not None
        assert status.job_id == "job1"

    def test_get_job_status_nonexistent(
        self, scheduler_parts: tuple[TaskScheduler, MagicMock]
    ) -> None:
        scheduler, mock_sched = scheduler_parts
        mock_sched.get_job.return_value = None
        assert scheduler.get_job_status("ghost") is None


# ---------------------------------------------------------------------------
# run_job_now
# ---------------------------------------------------------------------------


class TestRunJobNow:
    """run_job_now 测试。"""

    def test_run_existing_job(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        scheduler, mock_sched = scheduler_parts
        mock_job = MagicMock()
        mock_sched.get_job.return_value = mock_job
        scheduler.run_job_now("job1")
        mock_job.modify.assert_called_once()

    def test_run_nonexistent_job(self, scheduler_parts: tuple[TaskScheduler, MagicMock]) -> None:
        """不存在的任务不报错。"""
        scheduler, mock_sched = scheduler_parts
        mock_sched.get_job.return_value = None
        scheduler.run_job_now("ghost")
