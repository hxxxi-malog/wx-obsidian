"""采集结束后立即补抓缺正文的文章。

为什么需要它：上游对正文抓取失败只记 WARNING，文章仍以空正文、正常状态入库，
它自带的补抓队列每小时一轮、每轮最多 5 篇、单篇失败 3 次后放弃，于是「无正文」
最长能挂近一小时，失败后永久留空。采集阶段已经有微信原文兜底（见数据源补丁），
这里再把采集结束后仍缺正文的文章立刻补一遍，把窗口压到秒级。

触发时机：每一轮公众号采集结束时（扫码登录、手动采集、自动采集共用
`FeedSyncRunner`，由它收尾时回调）。上游采集是异步线程，新文章会在本轮补抓的
各轮之间陆续入库，所以补抓不是跑一遍就结束，而是按固定间隔多跑几轮。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import requests

from wx_obsidian.models import SourceArticle
from wx_obsidian.orchestrator import Orchestrator
from wx_obsidian.sources.base import SourceAuthError
from wx_obsidian.sources.we_mp_rss import CONTENT_STATE_PENDING

logger = logging.getLogger(__name__)

# 单篇补抓之间的间隔（秒）：上游每接一次刷新就真的去抓一次微信页面
REFRESH_INTERVAL = 2.0
# 两轮之间的等待（秒）：等上游后台线程把正文写回库
PASS_INTERVAL = 20.0
# 最多跑几轮：公众号采集是异步线程，新文章会在轮与轮之间陆续入库
MAX_PASSES = 12
# 前几轮即使没发现待补文章也要继续等（采集线程可能还没把新文章写进库）
MIN_PASSES = 3
# 单轮最多补抓多少篇，避免一次打太多微信请求
BATCH_LIMIT = 50


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ContentBackfillState:
    """补抓进度快照（字段名带 backfill_ 前缀，直接并进 /api/source/sync 的返回体）。"""

    running: bool = False
    pending: int = 0
    refreshed: int = 0
    failed: int = 0
    last_run_at: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "backfill_running": self.running,
            "backfill_pending": self.pending,
            "backfill_refreshed": self.refreshed,
            "backfill_failed": self.failed,
            "backfill_last_run_at": self.last_run_at,
            "backfill_error": self.error,
        }


class ContentBackfillRunner:
    """把数据源里缺正文的文章逐篇触发补抓（单飞）。"""

    def __init__(
        self,
        orchestrator: Orchestrator,
        *,
        refresh_interval: float = REFRESH_INTERVAL,
        pass_interval: float = PASS_INTERVAL,
        max_passes: int = MAX_PASSES,
        min_passes: int = MIN_PASSES,
        batch_limit: int = BATCH_LIMIT,
    ) -> None:
        self._orchestrator = orchestrator
        self._refresh_interval = max(0.0, float(refresh_interval))
        self._pass_interval = max(0.0, float(pass_interval))
        self._max_passes = max(1, int(max_passes))
        self._min_passes = min(max(1, int(min_passes)), self._max_passes)
        self._batch_limit = max(1, int(batch_limit))
        self._lock = threading.Lock()
        self._state = ContentBackfillState()

    def snapshot(self) -> dict[str, object]:
        """当前进度（复制一份，避免调用方读到半更新状态）。"""
        with self._lock:
            return ContentBackfillState(**asdict(self._state)).to_dict()

    def start(self) -> dict[str, object]:
        """开始一轮补抓；已有任务在跑时直接返回当前进度（单飞）。"""
        with self._lock:
            if self._state.running:
                return ContentBackfillState(**asdict(self._state)).to_dict()
            self._state = ContentBackfillState(running=True)
        threading.Thread(target=self._run, name="content-backfill", daemon=True).start()
        return self.snapshot()

    # -- 内部实现 -----------------------------------------------------------

    def _set(self, **fields: object) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._state, key, value)

    def _count(self, **deltas: int) -> None:
        with self._lock:
            for key, delta in deltas.items():
                setattr(self._state, key, getattr(self._state, key) + delta)

    def _fetch_pending(self) -> list[SourceArticle]:
        """取当前缺正文的文章（排除补抓失败的：它们需要用户手动重试）。"""
        page = self._orchestrator.list_source_articles(
            offset=0, limit=self._batch_limit, has_content=False
        )
        return [item for item in page.items if item.content_state == CONTENT_STATE_PENDING]

    def _refresh_all(self, pending: list[SourceArticle]) -> None:
        for index, article in enumerate(pending):
            # 上游的补抓接口只认它自己的内部 ID，article_id 是我们归一化后的短链 token
            target = article.upstream_id or article.article_id
            try:
                outcome = self._orchestrator.refresh_source_article(target)
            except SourceAuthError:
                raise
            except requests.RequestException as exc:
                logger.warning("触发正文补抓失败 (%s): %s", target, exc)
                self._count(failed=1)
            else:
                if outcome == "failed":
                    logger.warning("上游拒绝补抓正文 (%s)", target)
                    self._count(failed=1)
                else:
                    self._count(refreshed=1)
            if self._refresh_interval and index + 1 < len(pending):
                time.sleep(self._refresh_interval)

    def _run(self) -> None:
        try:
            for index in range(1, self._max_passes + 1):
                pending = self._fetch_pending()
                if index == 1:
                    with self._lock:
                        self._state.pending = len(pending)
                    if pending:
                        logger.info("采集结束，开始补抓 %d 篇缺正文的文章", len(pending))
                if pending:
                    self._refresh_all(pending)
                elif index >= self._min_passes:
                    break
                if index < self._max_passes:
                    time.sleep(self._pass_interval)
        except SourceAuthError as exc:
            logger.warning("正文补抓中止：%s", exc)
            self._set(error=str(exc))
        except Exception as exc:  # 后台线程：异常必须落进状态，不能静默消失
            logger.warning("正文补抓失败: %s", exc)
            self._set(error=f"{type(exc).__name__}: {exc}")
        finally:
            self._set(running=False, last_run_at=_now_iso())
            snapshot = self.snapshot()
            if snapshot["backfill_refreshed"] or snapshot["backfill_failed"]:
                logger.info(
                    "正文补抓完成：触发 %s 篇，失败 %s 篇",
                    snapshot["backfill_refreshed"],
                    snapshot["backfill_failed"],
                )
