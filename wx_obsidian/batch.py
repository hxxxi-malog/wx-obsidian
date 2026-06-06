"""批量并行处理：BatchProcessor, ResultCollector, ArchiveWriter。"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from wx_obsidian.config import load_max_workers, save_processed

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
    # 降级：检查错误消息中的关键词
    error_str = str(error).lower()
    retryable_patterns = ["timeout", "connection", "reset"]
    return any(pattern in error_str for pattern in retryable_patterns)


class BatchProcessor:
    """封装 ThreadPoolExecutor + as_completed() + 重试逻辑。"""

    def __init__(self, max_workers: int | None = None) -> None:
        self._max_workers = max_workers or load_max_workers()
        self._executor: ThreadPoolExecutor | None = None
        self._shutdown_requested = False

    def __enter__(self) -> BatchProcessor:
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._executor:
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
                    delay = 2 ** attempt
                    logger.warning(
                        "文章处理失败，重试 %d/%d (等待 %ds): %s - %s",
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
            "status": "failed",
            "error": str(last_error) if last_error else "unknown error",
        }


class ResultCollector:
    """线程安全地收集结果，保护 processed.json 并发写入。"""

    def __init__(self) -> None:
        self._results: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add_result(self, article_id: str, result: dict[str, Any]) -> None:
        """添加处理结果（线程安全）。"""
        with self._lock:
            self._results[article_id] = result

    def get_results(self) -> dict[str, dict[str, Any]]:
        """获取所有结果。"""
        with self._lock:
            return self._results.copy()

    def save(self, processed: dict[str, Any]) -> None:
        """保存结果到 processed.json（原子写入）。"""
        with self._lock:
            merged = {**processed, **self._results}
            save_processed(merged)


class ArchiveWriter:
    """线程安全地更新归档文件。"""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _get_lock(self, archive_path: str) -> threading.Lock:
        """获取归档文件对应的锁。"""
        with self._locks_lock:
            if archive_path not in self._locks:
                self._locks[archive_path] = threading.Lock()
            return self._locks[archive_path]

    def update_archive(
        self,
        vault_path: Any,
        date_str: str,
        title: str,
        category: str,
        summary: str,
    ) -> None:
        """更新归档文件（线程安全）。"""
        from wx_obsidian.output.vault import update_daily_archive

        # 解析日期：2026-06-05 -> 26/06/05
        parts = date_str.split("-")
        if len(parts) != 3:
            return
        yy, mm, dd = parts[0][2:], parts[1], parts[2]

        # 归档文件路径
        archive_dir = vault_path / "归档" / yy / mm
        archive_file = archive_dir / f"{dd}.md"

        # 获取锁
        lock = self._get_lock(str(archive_file))

        with lock:
            update_daily_archive(vault_path, date_str, title, category, summary)
