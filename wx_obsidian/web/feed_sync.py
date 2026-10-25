"""登录成功后触发上游采集：逐个公众号调用「更新」，单飞并记录进度。

为什么需要它：we-mp-rss 自己的定时任务只「补抓文章内容」，并不采集新文章；
重新扫码登录只是换了一份 Cookie，库里仍然没有新内容。账号页在扫码成功后调用
这里，把每个订阅的采集逐个触发一遍，公众号管理页刷新后才会出现新的更新时间。

并发约束：上游每接一次「更新」就起一个采集线程，两个触发之间留出间隔，避免
同时打几十个微信读书请求触发风控。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from wx_obsidian.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

# 两次触发之间的间隔（秒）
FEED_SYNC_INTERVAL = 3.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class FeedSyncState:
    """同步进度快照（直接序列化给前端）。"""

    running: bool = False
    total: int = 0
    triggered: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: str = ""
    finished_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FeedSyncRunner:
    """按顺序触发全部订阅的采集（单飞）。

    `on_finished` 在本轮触发流程结束时调用（扫码登录、手动采集、自动采集三种
    触发共用这一处收尾），用于紧接着补抓缺正文的文章。
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        interval: float = FEED_SYNC_INTERVAL,
        on_finished: Callable[[], object] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._interval = interval
        self._on_finished = on_finished
        self._lock = threading.Lock()
        self._state = FeedSyncState()

    def snapshot(self) -> FeedSyncState:
        """当前进度（复制一份，避免调用方读到半更新状态）。"""
        with self._lock:
            return FeedSyncState(**asdict(self._state))

    def start(self) -> FeedSyncState:
        """开始一次全量触发；已有任务在跑时直接返回当前进度（单飞）。"""
        with self._lock:
            if self._state.running:
                return FeedSyncState(**asdict(self._state))
            self._state = FeedSyncState(running=True, started_at=_now_iso())

        threading.Thread(target=self._run, name="feed-sync", daemon=True).start()
        return self.snapshot()

    def _run(self) -> None:
        try:
            feeds = self._orchestrator.get_feeds()
            with self._lock:
                self._state.total = len(feeds)

            for index, feed in enumerate(feeds):
                try:
                    outcome = self._orchestrator.refresh_feed(feed.id)
                except Exception as exc:  # 单个号失败不能拖垮整轮，但要记进状态
                    logger.warning("触发采集失败 (%s): %s", feed.id, exc)
                    with self._lock:
                        self._state.failed += 1
                        self._state.error = self._state.error or f"{type(exc).__name__}: {exc}"
                else:
                    with self._lock:
                        if outcome == "triggered":
                            self._state.triggered += 1
                        elif outcome == "skipped":
                            self._state.skipped += 1
                        else:
                            self._state.failed += 1
                if index + 1 < len(feeds):
                    time.sleep(self._interval)
        except Exception as exc:  # 后台线程：异常必须落进状态，不能静默消失
            logger.warning("登录后触发采集失败: %s", exc)
            with self._lock:
                self._state.error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self._state.running = False
                self._state.finished_at = _now_iso()
            # 触发流程已结束：紧接着补抓缺正文的文章。上游采集是异步线程，
            # 新文章会在补抓的各轮之间陆续入库（见 content_backfill）。
            if self._on_finished is not None:
                self._on_finished()
