"""批量并行处理：BatchProcessor, ArchiveWriter。"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from wx_obsidian.config import load_max_workers

logger = logging.getLogger(__name__)

# 重试配置
MAX_RETRIES = 3
RETRYABLE_ERRORS = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
)

RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def _is_retryable_error(error: Exception) -> bool:
    """判断错误是否可重试。"""
    if isinstance(error, RETRYABLE_ERRORS):
        return True
    # 检查 HTTP 状态码
    try:
        import requests

        if isinstance(error, requests.HTTPError) and error.response is not None:
            return error.response.status_code in RETRYABLE_HTTP_CODES
    except ImportError:
        pass
    return False


class BatchProcessor:
    """封装 ThreadPoolExecutor + as_completed() + 重试逻辑。"""

    def __init__(
        self,
        max_workers: int | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._max_workers = max_workers or load_max_workers()
        self._executor: ThreadPoolExecutor | None = None
        self._external_executor = executor is not None
        self._provided_executor = executor
        self._shutdown_requested = False

    def __enter__(self) -> BatchProcessor:
        if self._provided_executor:
            self._executor = self._provided_executor
        else:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._executor and not self._external_executor:
            self._executor.shutdown(wait=True)
        self._executor = None

    def request_shutdown(self) -> None:
        """请求关闭，停止提交新任务。"""
        self._shutdown_requested = True

    def process_articles(
        self,
        articles: list[dict[str, Any]],
        process_func: Callable[[dict[str, Any]], dict[str, Any]],
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[dict[str, Any]]:
        """并行处理文章列表。

        Args:
            articles: 文章列表
            process_func: 处理函数，接收文章字典，返回结果字典
            on_complete: 每篇文章完成时的回调函数

        Returns:
            处理结果列表
        """
        if not self._executor:
            raise RuntimeError("BatchProcessor 未初始化，请使用 with 语句")

        results: list[dict[str, Any]] = []
        futures_to_article: dict[Any, dict[str, Any]] = {}

        # 提交所有任务
        for article in articles:
            if self._shutdown_requested:
                break
            future = self._executor.submit(self._process_with_retry, article, process_func)
            futures_to_article[future] = article

        # 使用 as_completed 处理完成的任务
        for future in as_completed(futures_to_article):
            article = futures_to_article[future]
            try:
                result = future.result()
                results.append(result)
                if on_complete:
                    on_complete(result)
            except Exception as e:
                logger.error("文章处理失败: %s - %s", article.get("title", "未知"), e)
                result = {
                    "article_id": article.get("id", "unknown"),
                    "title": article.get("title", "未知"),
                    "status": "failed",
                    "error": str(e),
                }
                results.append(result)
                if on_complete:
                    on_complete(result)

        return results

    def _process_with_retry(
        self,
        article: dict[str, Any],
        process_func: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """带重试的文章处理。"""
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            if self._shutdown_requested:
                return {
                    "article_id": article.get("id", "unknown"),
                    "status": "cancelled",
                    "error": "shutdown requested",
                }

            try:
                result = process_func(article)
                return result
            except Exception as e:
                last_error = e
                if _is_retryable_error(e) and attempt < MAX_RETRIES - 1:
                    base_delay = 2**attempt
                    jitter = random.uniform(0, base_delay * 0.1)
                    delay = base_delay + jitter
                    logger.warning(
                        "文章处理失败，重试 %d/%d (等待 %.1fs): %s - %s",
                        attempt + 1,
                        MAX_RETRIES,
                        delay,
                        article.get("title", "未知"),
                        e,
                    )
                    time.sleep(delay)
                    continue
                else:
                    # 不可重试错误或达到最大重试次数
                    break

        return {
            "article_id": article.get("id", "unknown"),
            "title": article.get("title", "未知"),
            "status": "failed",
            "error": str(last_error) if last_error else "unknown error",
        }


class ArchiveWriter:
    """线程安全地更新归档文件（使用单把全局锁）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def update_archive(
        self,
        vault_path: Path,
        date_str: str,
        title: str,
        category: str,
        summary: str,
    ) -> None:
        """更新归档文件（线程安全）。"""
        from wx_obsidian.output.vault import update_daily_archive

        with self._lock:
            update_daily_archive(vault_path, date_str, title, category, summary)
