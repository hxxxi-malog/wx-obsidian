from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from wx_obsidian.web.feed_sync import FeedSyncRunner

if TYPE_CHECKING:
    from wx_obsidian.web.jobs import JobManager

logger = logging.getLogger(__name__)

# 服务启动后到首轮采集的延迟（秒）。太短会和启动期的探活、页面首屏请求叠在一起。
DEFAULT_INITIAL_DELAY_SECONDS = 10.0
# 等待一轮采集结束时的轮询间隔（秒）
WAIT_POLL_SECONDS = 1.0
# 单轮采集的等待上限（秒）：上游采集线程卡住时不能让调度线程永久停摆
WAIT_TIMEOUT_SECONDS = 15 * 60


@dataclass
class SourceSyncState:
    """自动采集状态快照（叠加进 GET /api/source/sync 的返回体）。"""

    auto_enabled: bool = True
    auto_interval_minutes: int = 0
    next_run_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SourceSyncScheduler:
    """按固定间隔触发数据源采集的后台调度器。

    上游只在收到「更新」请求时采集新文章，而扫码登录成功只代表 Cookie 可用，
    不会带来新文章。这个调度器每隔一段时间复用 `FeedSyncRunner` 触发一轮采集，
    保证「最新文章」页面与抓取管线看到的数据源文章库持续更新。

    只调用上游的「更新」接口，不写 `processed.json` 与 Vault；抓取任务运行期间
    跳过本轮，避免两类上游请求同时进行。
    """

    def __init__(
        self,
        runner: FeedSyncRunner,
        *,
        jobs: JobManager,
        interval_seconds: float,
        interval_minutes: int,
        initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
    ) -> None:
        self._runner = runner
        self._jobs = jobs
        self._interval = max(1.0, float(interval_seconds))
        self._initial_delay = max(0.0, float(initial_delay_seconds))
        self._state = SourceSyncState(auto_interval_minutes=max(1, int(interval_minutes)))
        self._state_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- 生命周期 -----------------------------------------------------------

    def start(self) -> None:
        """启动后台线程（重复调用无副作用）。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="source-sync", daemon=True)
        self._thread.start()
        logger.info(
            "数据源自动采集已启动：每 %d 分钟触发一轮，%d 秒后执行首轮",
            self._state.auto_interval_minutes,
            int(self._initial_delay),
        )

    def stop(self, timeout: float = 5.0) -> None:
        """通知调度线程退出，并等待它结束。"""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout)

    # -- 状态与单轮 ---------------------------------------------------------

    def snapshot(self) -> dict[str, object]:
        """当前状态（下次触发时间按 UTC ISO 8601 输出，前端自行本地化）。"""
        with self._state_lock:
            return self._state.to_dict()

    def run_once(self) -> bool:
        """执行一轮采集；因单飞或抓取任务占用而跳过时返回 False。"""
        if self._runner.snapshot().running:
            logger.info("上一轮数据源采集仍在进行，跳过本轮自动采集")
            return False
        if self._jobs.busy:
            logger.info("抓取任务运行中，跳过本轮自动采集")
            return False
        self._runner.start()
        return True

    # -- 内部实现 -----------------------------------------------------------

    def _wait_round_finished(self) -> None:
        """等本轮触发流程结束；超时后进入下一轮，避免线程永久停摆。"""
        deadline = datetime.now(UTC) + timedelta(seconds=WAIT_TIMEOUT_SECONDS)
        while not self._stop.is_set() and datetime.now(UTC) < deadline:
            if not self._runner.snapshot().running:
                return
            if self._stop.wait(WAIT_POLL_SECONDS):
                return
        logger.warning("等待本轮数据源采集结束超时，继续下一轮调度")

    def _log_round_result(self) -> None:
        state = self._runner.snapshot()
        if state.failed:
            logger.warning(
                "自动采集完成：已触发 %d 个公众号，跳过 %d 个，失败 %d 个",
                state.triggered,
                state.skipped,
                state.failed,
            )
        else:
            logger.info(
                "自动采集完成：已触发 %d 个公众号，跳过 %d 个",
                state.triggered,
                state.skipped,
            )

    def _schedule_next(self) -> None:
        next_run = datetime.now(UTC) + timedelta(seconds=self._interval)
        with self._state_lock:
            self._state.next_run_at = next_run.isoformat(timespec="seconds")

    def _loop(self) -> None:
        if self._stop.wait(self._initial_delay):
            return
        while not self._stop.is_set():
            self.run_once()
            self._wait_round_finished()
            self._log_round_result()
            self._schedule_next()
            if self._stop.wait(self._interval):
                return
