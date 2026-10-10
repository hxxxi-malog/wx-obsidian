"""batch.py 的单元测试和集成测试。"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from wx_obsidian.batch import (
    MAX_RETRIES,
    ArchiveWriter,
    BatchProcessor,
    _is_retryable_error,
)

# ---------------------------------------------------------------------------
# _is_retryable_error 测试
# ---------------------------------------------------------------------------


class TestIsRetryableError:
    """_is_retryable_error 函数测试。"""

    def test_timeout_error_is_retryable(self) -> None:
        """TimeoutError 应该是可重试的。"""
        assert _is_retryable_error(TimeoutError("timeout")) is True

    def test_connection_error_is_retryable(self) -> None:
        """ConnectionError 应该是可重试的。"""
        assert _is_retryable_error(ConnectionError("connection failed")) is True

    def test_connection_reset_error_is_retryable(self) -> None:
        """ConnectionResetError 应该是可重试的。"""
        assert _is_retryable_error(ConnectionResetError("reset")) is True

    def test_http_429_is_retryable(self) -> None:
        """HTTP 429 错误应该是可重试的。"""
        # 需要使用 requests.HTTPError 来测试
        import requests

        response = requests.Response()
        response.status_code = 429
        error = requests.HTTPError("429 Too Many Requests", response=response)
        assert _is_retryable_error(error) is True

    def test_http_500_is_retryable(self) -> None:
        """HTTP 500 错误应该是可重试的。"""
        import requests

        response = requests.Response()
        response.status_code = 500
        error = requests.HTTPError("500 Internal Server Error", response=response)
        assert _is_retryable_error(error) is True

    def test_value_error_is_not_retryable(self) -> None:
        """ValueError 不应该是可重试的。"""
        assert _is_retryable_error(ValueError("invalid value")) is False

    def test_key_error_is_not_retryable(self) -> None:
        """KeyError 不应该是可重试的。"""
        assert _is_retryable_error(KeyError("key not found")) is False

    def test_empty_message_is_not_retryable(self) -> None:
        """空异常消息不应该是可重试的。"""
        assert _is_retryable_error(Exception("")) is False


# ---------------------------------------------------------------------------
# BatchProcessor 测试
# ---------------------------------------------------------------------------


class TestBatchProcessor:
    """BatchProcessor 类测试。"""

    @patch("wx_obsidian.batch.load_max_workers")
    def test_init_with_default_workers(self, mock_load: MagicMock) -> None:
        """测试默认并行度加载。"""
        mock_load.return_value = 5
        processor = BatchProcessor()
        assert processor._max_workers == 5

    @patch("wx_obsidian.batch.load_max_workers")
    def test_init_with_custom_workers(self, mock_load: MagicMock) -> None:
        """测试自定义并行度。"""
        mock_load.return_value = 5
        processor = BatchProcessor(max_workers=3)
        assert processor._max_workers == 3

    @patch("wx_obsidian.batch.load_max_workers")
    def test_context_manager(self, mock_load: MagicMock) -> None:
        """测试上下文管理器。"""
        mock_load.return_value = 2
        with BatchProcessor() as processor:
            assert processor._executor is not None
        assert processor._executor is None

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_articles_without_context(self, mock_load: MagicMock) -> None:
        """测试未使用上下文管理器时抛出异常。"""
        mock_load.return_value = 2
        processor = BatchProcessor()
        with pytest.raises(RuntimeError, match="BatchProcessor 未初始化"):
            processor.process_articles([], lambda x: x)

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_empty_articles(self, mock_load: MagicMock) -> None:
        """测试空文章列表。"""
        mock_load.return_value = 2
        with BatchProcessor() as processor:
            results = processor.process_articles([], lambda x: x)
        assert results == []

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_single_article(self, mock_load: MagicMock) -> None:
        """测试单篇文章处理。"""
        mock_load.return_value = 2

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            return {"article_id": article["id"], "status": "done"}

        with BatchProcessor() as processor:
            results = processor.process_articles([{"id": "1", "title": "test"}], process_func)

        assert len(results) == 1
        assert results[0]["article_id"] == "1"
        assert results[0]["status"] == "done"

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_multiple_articles(self, mock_load: MagicMock) -> None:
        """测试多篇文章并行处理。"""
        mock_load.return_value = 2

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            return {"article_id": article["id"], "status": "done"}

        articles = [{"id": str(i), "title": f"test {i}"} for i in range(5)]
        with BatchProcessor() as processor:
            results = processor.process_articles(articles, process_func)

        assert len(results) == 5
        for result in results:
            assert result["status"] == "done"

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_with_retryable_error(self, mock_load: MagicMock) -> None:
        """测试可重试错误重试。"""
        mock_load.return_value = 2
        call_count = 0

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError("timeout")
            return {"article_id": article["id"], "status": "done"}

        with BatchProcessor() as processor:
            results = processor.process_articles([{"id": "1", "title": "test"}], process_func)

        assert len(results) == 1
        assert results[0]["status"] == "done"
        assert call_count == 3

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_with_non_retryable_error(self, mock_load: MagicMock) -> None:
        """测试不可重试错误不重试。"""
        mock_load.return_value = 2
        call_count = 0

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid value")

        with BatchProcessor() as processor:
            results = processor.process_articles([{"id": "1", "title": "test"}], process_func)

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert call_count == 1

    @patch("wx_obsidian.batch.load_max_workers")
    def test_process_with_max_retries_exceeded(self, mock_load: MagicMock) -> None:
        """测试超过最大重试次数。"""
        mock_load.return_value = 2
        call_count = 0

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            raise TimeoutError("timeout")

        with BatchProcessor() as processor:
            results = processor.process_articles([{"id": "1", "title": "test"}], process_func)

        assert len(results) == 1
        assert results[0]["status"] == "failed"
        assert call_count == MAX_RETRIES

    @patch("wx_obsidian.batch.load_max_workers")
    def test_request_shutdown(self, mock_load: MagicMock) -> None:
        """测试请求关闭。"""
        mock_load.return_value = 2

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            return {"article_id": article["id"], "status": "done"}

        with BatchProcessor() as processor:
            processor.request_shutdown()
            results = processor.process_articles([{"id": "1", "title": "test"}], process_func)

        assert len(results) == 0

    @patch("wx_obsidian.batch.load_max_workers")
    def test_shutdown_during_processing(self, mock_load: MagicMock) -> None:
        """测试处理过程中请求关闭。"""
        mock_load.return_value = 2
        call_count = 0

        def process_func(article: dict[str, Any]) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # 第一篇文章处理时触发关闭
                time.sleep(0.1)
            return {"article_id": article["id"], "status": "done"}

        articles = [{"id": str(i), "title": f"test {i}"} for i in range(5)]
        with BatchProcessor() as processor:
            # 在另一个线程中请求关闭
            def request_shutdown_after_delay() -> None:
                time.sleep(0.05)
                processor.request_shutdown()

            shutdown_thread = threading.Thread(target=request_shutdown_after_delay)
            shutdown_thread.start()

            results = processor.process_articles(articles, process_func)
            shutdown_thread.join()

        # 应该有一些文章被处理，一些被取消
        assert len(results) > 0
        # 检查是否有取消的文章
        done = [r for r in results if r.get("status") == "done"]
        # 至少有一些文章被处理
        assert len(done) > 0


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# ArchiveWriter 测试
# ---------------------------------------------------------------------------


class TestArchiveWriter:
    """ArchiveWriter 类测试。"""

    def test_update_archive_invalid_date(self, tmp_path: Path) -> None:
        """测试无效日期格式。"""
        writer = ArchiveWriter()
        writer.update_archive(tmp_path, "invalid-date", "title", "category", "summary")
        # 不应该创建任何文件
        assert not (tmp_path / "公众号文章" / "Z归档").exists()

    def test_update_archive_new_file(self, tmp_path: Path) -> None:
        """测试创建新归档文件。"""
        writer = ArchiveWriter()
        writer.update_archive(tmp_path, "2026-06-05", "测试文章", "LLM", "这是摘要")

        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        assert archive_file.exists()
        content = archive_file.read_text(encoding="utf-8")
        assert "# 2026-06-05 文章归档" in content
        assert "测试文章" in content
        assert "这是摘要" in content

    def test_update_archive_append_to_existing(self, tmp_path: Path) -> None:
        """测试追加到已有归档文件。"""
        writer = ArchiveWriter()
        writer.update_archive(tmp_path, "2026-06-05", "文章1", "LLM", "摘要1")
        writer.update_archive(tmp_path, "2026-06-05", "文章2", "Agent", "摘要2")

        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        content = archive_file.read_text(encoding="utf-8")
        assert "文章1" in content
        assert "文章2" in content

    def test_update_archive_skip_duplicate(self, tmp_path: Path) -> None:
        """测试跳过重复条目。"""
        writer = ArchiveWriter()
        writer.update_archive(tmp_path, "2026-06-05", "测试文章", "LLM", "这是摘要")
        writer.update_archive(tmp_path, "2026-06-05", "测试文章", "LLM", "这是摘要")

        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        content = archive_file.read_text(encoding="utf-8")
        # 检查条目只出现一次（链接路径和链接文本各算一次，所以是 2）
        assert content.count("[[LLM/测试文章|测试文章]]") == 1

    def test_update_archive_long_summary_kept_intact(self, tmp_path: Path) -> None:
        """分组归档保留完整摘要（dfbc926 重构移除了旧的 [:100] + "..." 截断）。"""
        writer = ArchiveWriter()
        long_summary = "a" * 200
        writer.update_archive(tmp_path, "2026-06-05", "测试文章", "LLM", long_summary)

        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        content = archive_file.read_text(encoding="utf-8")
        # 摘要作为条目下的缩进行完整写入，未被截断
        assert f"\n  {long_summary}\n" in content

    def test_update_archive_special_chars_in_title(self, tmp_path: Path) -> None:
        """测试标题中的特殊字符。"""
        writer = ArchiveWriter()
        writer.update_archive(tmp_path, "2026-06-05", "测试:文章/标题", "LLM", "摘要")

        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        assert archive_file.exists()
        content = archive_file.read_text(encoding="utf-8")
        assert "测试_文章_标题" in content

    def test_concurrent_update_same_archive(self, tmp_path: Path) -> None:
        """测试并发更新同一归档文件。"""
        writer = ArchiveWriter()
        errors: list[Exception] = []

        def update_archive(i: int) -> None:
            try:
                writer.update_archive(tmp_path, "2026-06-05", f"文章{i}", "LLM", f"摘要{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=update_archive, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        archive_file = tmp_path / "公众号文章" / "Z归档" / "26" / "06" / "05.md"
        content = archive_file.read_text(encoding="utf-8")
        for i in range(10):
            assert f"文章{i}" in content
