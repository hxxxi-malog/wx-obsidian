"""登录后触发采集（FeedSyncRunner）单元测试：顺序、计数、单飞、错误可见。"""

from __future__ import annotations

import threading
import time

from wx_obsidian.models import Feed
from wx_obsidian.web.feed_sync import FeedSyncRunner


class _StubOrchestrator:
    def __init__(
        self,
        feeds: list[Feed],
        outcomes: list[str] | None = None,
        gate: threading.Event | None = None,
        fail_ids: set[str] | None = None,
    ) -> None:
        self.feeds = feeds
        self.outcomes = outcomes or ["triggered"] * len(feeds)
        self.gate = gate
        self.fail_ids = fail_ids or set()
        self.refreshed: list[str] = []
        self._lock = threading.Lock()

    def get_feeds(self) -> list[Feed]:
        return list(self.feeds)

    def refresh_feed(self, feed_id: str) -> str:
        with self._lock:
            index = len(self.refreshed)
            self.refreshed.append(feed_id)
        if self.gate is not None:
            self.gate.wait(5)
        if feed_id in self.fail_ids:
            raise ConnectionError(f"{feed_id} 连不上")
        return self.outcomes[index] if index < len(self.outcomes) else "triggered"


def _wait_finished(runner: FeedSyncRunner, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = runner.snapshot()
        if not state.running:
            return state
        time.sleep(0.01)
    raise AssertionError("同步没有在超时前结束")


def test_triggers_every_feed_in_order() -> None:
    feeds = [Feed(id=f"f{i}", name=f"号{i}") for i in (1, 2, 3)]
    orchestrator = _StubOrchestrator(feeds)
    runner = FeedSyncRunner(orchestrator, interval=0)  # type: ignore[arg-type]

    runner.start()
    state = _wait_finished(runner)

    assert orchestrator.refreshed == ["f1", "f2", "f3"]
    assert state.total == 3
    assert state.triggered == 3
    assert state.failed == 0
    assert state.started_at
    assert state.finished_at


def test_counts_skipped_and_failed() -> None:
    feeds = [Feed(id=f"f{i}", name=f"号{i}") for i in (1, 2, 3)]
    orchestrator = _StubOrchestrator(feeds, outcomes=["triggered", "skipped", "failed"])
    runner = FeedSyncRunner(orchestrator, interval=0)  # type: ignore[arg-type]

    runner.start()
    state = _wait_finished(runner)

    assert (state.triggered, state.skipped, state.failed) == (1, 1, 1)


def test_single_flight() -> None:
    """上一轮没跑完时再点一次，不会并行起第二轮。"""
    gate = threading.Event()
    feeds = [Feed(id="f1", name="一号"), Feed(id="f2", name="二号")]
    orchestrator = _StubOrchestrator(feeds, gate=gate)
    runner = FeedSyncRunner(orchestrator, interval=0)  # type: ignore[arg-type]

    runner.start()
    time.sleep(0.05)
    second = runner.start()  # 此时第一轮卡在 gate 上
    gate.set()
    state = _wait_finished(runner)

    assert second.running is True
    assert orchestrator.refreshed == ["f1", "f2"]
    assert state.triggered == 2


def test_single_feed_failure_does_not_stop_the_rest() -> None:
    feeds = [Feed(id="f1", name="一号"), Feed(id="f2", name="二号")]
    orchestrator = _StubOrchestrator(feeds, fail_ids={"f1"})
    runner = FeedSyncRunner(orchestrator, interval=0)  # type: ignore[arg-type]

    runner.start()
    state = _wait_finished(runner)

    assert orchestrator.refreshed == ["f1", "f2"]
    assert state.failed == 1
    assert state.triggered == 1
    assert "连不上" in state.error


class _BrokenFeeds(_StubOrchestrator):
    def get_feeds(self) -> list[Feed]:
        raise ConnectionError("数据源不可达")


def test_get_feeds_failure_is_recorded() -> None:
    runner = FeedSyncRunner(_BrokenFeeds([]), interval=0)  # type: ignore[arg-type]

    runner.start()
    state = _wait_finished(runner)

    assert state.total == 0
    assert state.running is False
    assert "数据源不可达" in state.error
