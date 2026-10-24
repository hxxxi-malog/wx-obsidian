"""数据源自动采集调度器测试：单飞、跳过条件、周期推进、退出。"""

from __future__ import annotations

import time

from wx_obsidian.web.feed_sync import FeedSyncState
from wx_obsidian.web.source_sync import SourceSyncScheduler


class _StubRunner:
    """可立即结束的 FeedSyncRunner 替身。"""

    def __init__(self) -> None:
        self.starts = 0
        self.running = False
        self.state = FeedSyncState(total=2, triggered=2)

    def snapshot(self) -> FeedSyncState:
        self.state.running = self.running
        return self.state

    def start(self) -> FeedSyncState:
        self.starts += 1
        self.running = False
        return self.state


class _StubJobs:
    def __init__(self, busy: bool = False) -> None:
        self._busy = busy

    @property
    def busy(self) -> bool:
        return self._busy


def _scheduler(
    runner: _StubRunner,
    jobs: _StubJobs,
    *,
    interval: float = 0.05,
    initial: float = 0.0,
) -> SourceSyncScheduler:
    return SourceSyncScheduler(
        runner,  # type: ignore[arg-type]
        jobs=jobs,  # type: ignore[arg-type]
        interval_seconds=interval,
        interval_minutes=30,
        initial_delay_seconds=initial,
    )


def test_run_once_triggers_when_idle() -> None:
    runner = _StubRunner()
    scheduler = _scheduler(runner, _StubJobs())

    assert scheduler.run_once() is True
    assert runner.starts == 1


def test_run_once_skips_when_previous_round_running() -> None:
    runner = _StubRunner()
    runner.running = True
    scheduler = _scheduler(runner, _StubJobs())

    assert scheduler.run_once() is False
    assert runner.starts == 0


def test_run_once_skips_while_pipeline_job_running() -> None:
    runner = _StubRunner()
    scheduler = _scheduler(runner, _StubJobs(busy=True))

    assert scheduler.run_once() is False
    assert runner.starts == 0


def test_initial_delay_postpones_first_round() -> None:
    runner = _StubRunner()
    scheduler = _scheduler(runner, _StubJobs(), interval=10, initial=1.0)
    scheduler.start()
    time.sleep(0.05)
    scheduler.stop()

    assert runner.starts == 0


def test_loop_advances_next_run_and_stops() -> None:
    runner = _StubRunner()
    scheduler = _scheduler(runner, _StubJobs(), interval=0.02, initial=0.0)
    scheduler.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and runner.starts < 2:
            time.sleep(0.01)
        assert runner.starts >= 2
        assert scheduler.snapshot()["next_run_at"]
    finally:
        scheduler.stop()

    state = scheduler.snapshot()
    assert state["auto_enabled"] is True
    assert state["auto_interval_minutes"] == 30


def test_stop_is_idempotent() -> None:
    scheduler = _scheduler(_StubRunner(), _StubJobs(), interval=10, initial=10)
    scheduler.start()
    scheduler.stop()
    scheduler.stop()
