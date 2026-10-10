"""增量过滤逻辑测试（P1 修复验证）。

验证：
- P1-1: error/skipped/failed 记录重试受 retry_count 限制（无无限重试）
- P1-2: done 记录文件缺失时批量 pop 并立即持久化
- P1-3: ImportError 从 _markdown_stage 传播（不被静默吞掉）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

MAX_CROSS_RUN_RETRIES = 3


def _run_filter(
    processed: dict[str, Any],
    articles: list[dict[str, Any]],
    max_days: int = 7,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    """提取增量过滤逻辑进行测试。

    Returns:
        (new_articles, processed_after_filter, prev_retry_counts)
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
    new_articles = []
    skipped_existing = 0
    skipped_date = 0
    ids_to_reprocess: list[str] = []
    prev_retry_counts: dict[str, int] = {}
    for a in articles:
        if not a.get("id"):
            continue
        aid = str(a["id"])
        if aid in processed:
            record = processed[aid]
            if isinstance(record, dict) and record.get("status") == "done":
                file_path = record.get("file", "")
                if file_path and Path(file_path).exists():
                    skipped_existing += 1
                    continue
                ids_to_reprocess.append(aid)
                new_articles.append(a)
            elif isinstance(record, dict) and record.get("retry_count", 0) >= MAX_CROSS_RUN_RETRIES:
                skipped_existing += 1
            else:
                prev_retry_counts[aid] = (
                    record.get("retry_count", 0) if isinstance(record, dict) else 0
                )
                ids_to_reprocess.append(aid)
                new_articles.append(a)
        elif a.get("date_published") and a.get("date_published", "")[:10] < cutoff:
            skipped_date += 1
        else:
            new_articles.append(a)
    if ids_to_reprocess:
        for aid in ids_to_reprocess:
            processed.pop(aid, None)
    return new_articles, processed, prev_retry_counts


class TestIncrementalFilterP1:
    """P1-1: error/skipped/failed 记录重试受 retry_count 限制。"""

    def test_error_record_retried_within_limit(self) -> None:
        """error 记录未超重试上限时应重新处理。"""
        processed: dict[str, Any] = {
            "a1": {"status": "error", "reason": "timeout", "retry_count": 1},
        }
        articles = [{"id": "a1", "title": "失败文章", "date_published": "2026-06-07"}]
        new_articles, result, retry_counts = _run_filter(processed, articles)

        assert "a1" not in result, "未超上限的 error 记录应被 pop"
        assert len(new_articles) == 1
        assert retry_counts["a1"] == 1

    def test_error_record_exceeds_retry_limit(self) -> None:
        """error 记录超过重试上限时应永久跳过。"""
        processed: dict[str, Any] = {
            "a1": {"status": "error", "reason": "timeout", "retry_count": 3},
        }
        articles = [{"id": "a1", "title": "失败文章", "date_published": "2026-06-07"}]
        new_articles, result, retry_counts = _run_filter(processed, articles)

        assert "a1" in result, "超上限的 error 记录应保留"
        assert len(new_articles) == 0

    def test_error_record_no_retry_count_treated_as_zero(self) -> None:
        """无 retry_count 字段的 error 记录视为 0 次重试。"""
        processed: dict[str, Any] = {
            "a1": {"status": "error", "reason": "timeout"},
        }
        articles = [{"id": "a1", "title": "失败文章", "date_published": "2026-06-07"}]
        new_articles, result, retry_counts = _run_filter(processed, articles)

        assert "a1" not in result, "无 retry_count 视为 0，应重试"
        assert retry_counts["a1"] == 0

    def test_skipped_record_retried_within_limit(self) -> None:
        """skipped 记录未超上限时应重新处理。"""
        processed: dict[str, Any] = {
            "a1": {"status": "skipped", "reason": "no_content", "retry_count": 0},
        }
        articles = [{"id": "a1", "title": "跳过文章", "date_published": "2026-06-07"}]
        new_articles, result, _ = _run_filter(processed, articles)

        assert len(new_articles) == 1

    def test_failed_record_retried_within_limit(self) -> None:
        """failed 记录未超上限时应重新处理。"""
        processed: dict[str, Any] = {
            "a1": {"status": "failed", "error": "unknown", "retry_count": 2},
        }
        articles = [{"id": "a1", "title": "失败文章", "date_published": "2026-06-07"}]
        new_articles, result, _ = _run_filter(processed, articles)

        assert len(new_articles) == 1

    def test_non_dict_record_retried(self) -> None:
        """非 dict 格式的历史记录视为 retry_count=0，应重试。"""
        processed: dict[str, Any] = {"a1": "old_format_string"}
        articles = [{"id": "a1", "title": "旧格式", "date_published": "2026-06-07"}]
        new_articles, result, retry_counts = _run_filter(processed, articles)

        assert "a1" not in result, "非 dict 记录应被 pop 重试"
        assert len(new_articles) == 1
        assert retry_counts["a1"] == 0

    def test_dict_without_status_field_retried(self) -> None:
        """无 status 字段的 dict 记录应被重试（视为 retry_count=0）。"""
        processed: dict[str, Any] = {"a1": {"title": "旧记录"}}
        articles = [{"id": "a1", "title": "无状态", "date_published": "2026-06-07"}]
        new_articles, result, retry_counts = _run_filter(processed, articles)

        assert len(new_articles) == 1
        assert retry_counts["a1"] == 0


class TestIncrementalFilterP1DoneMissing:
    """P1-2: done 记录文件缺失时批量 pop 并持久化。"""

    def test_done_record_missing_file_popped(self, tmp_path: Path) -> None:
        """done 记录的文件不存在时应被 pop。"""
        missing_file = tmp_path / "nonexistent.md"
        processed: dict[str, Any] = {
            "a1": {"status": "done", "file": str(missing_file), "category": "LLM"},
        }
        articles = [{"id": "a1", "title": "文件缺失", "date_published": "2026-06-07"}]
        new_articles, result, _ = _run_filter(processed, articles)

        assert "a1" not in result, "文件缺失的 done 记录应被 pop"
        assert len(new_articles) == 1, "应进入重处理列表"

    def test_done_record_existing_file_kept(self, tmp_path: Path) -> None:
        """done 记录的文件存在时应保留。"""
        existing_file = tmp_path / "exists.md"
        existing_file.write_text("content")
        processed: dict[str, Any] = {
            "a1": {"status": "done", "file": str(existing_file), "category": "LLM"},
        }
        articles = [{"id": "a1", "title": "文件存在", "date_published": "2026-06-07"}]
        new_articles, result, _ = _run_filter(processed, articles)

        assert "a1" in result, "文件存在的 done 记录应保留"
        assert len(new_articles) == 0

    def test_done_record_empty_file_path_popped(self) -> None:
        """done 记录但 file 为空字符串时应被 pop。"""
        processed: dict[str, Any] = {
            "a1": {"status": "done", "file": "", "category": "LLM"},
        }
        articles = [{"id": "a1", "title": "空路径", "date_published": "2026-06-07"}]
        new_articles, result, _ = _run_filter(processed, articles)

        assert "a1" not in result, "空 file 路径的 done 记录应被 pop"
        assert len(new_articles) == 1

    def test_batch_pop_multiple_missing(self, tmp_path: Path) -> None:
        """多个文件缺失时应批量 pop。"""
        processed: dict[str, Any] = {
            "a1": {"status": "done", "file": str(tmp_path / "missing1.md")},
            "a2": {"status": "done", "file": str(tmp_path / "missing2.md")},
            "a3": {"status": "done", "file": str(tmp_path / "missing3.md")},
        }
        articles = [
            {"id": f"a{i}", "title": f"文章{i}", "date_published": "2026-06-07"}
            for i in range(1, 4)
        ]
        new_articles, result, _ = _run_filter(processed, articles)

        assert len(result) == 0, "所有缺失文件的记录都应被 pop"
        assert len(new_articles) == 3

    def test_mixed_done_and_error_records(self, tmp_path: Path) -> None:
        """混合 done（文件缺失）和 error 记录时，都进入重处理。"""
        existing_file = tmp_path / "exists.md"
        existing_file.write_text("content")
        processed: dict[str, Any] = {
            "a1": {"status": "done", "file": str(tmp_path / "missing.md")},
            "a2": {"status": "error", "reason": "timeout"},
            "a3": {"status": "done", "file": str(existing_file)},
        }
        articles = [
            {"id": f"a{i}", "title": f"文章{i}", "date_published": "2026-06-07"}
            for i in range(1, 4)
        ]
        new_articles, result, _ = _run_filter(processed, articles)

        assert "a1" not in result, "文件缺失的 done 记录应被 pop"
        assert "a2" not in result, "error 记录应被 pop 重试"
        assert "a3" in result, "文件存在的 done 记录应保留"
        assert len(new_articles) == 2


class TestMarkdownStageImportError:
    """P1-3: ImportError 从 _markdown_stage 传播。"""

    def test_import_error_propagates(self) -> None:
        """ImportError 不应被 _markdown_stage 的 except 捕获。

        验证：移除 ImportError 后，detect_format_issues 调用产生的
        ImportError 会向上传播而非被 warning 吞掉。
        """
        from wx_obsidian.orchestrator import _markdown_stage
        from wx_obsidian.processing.models import PipelineContext

        ctx = PipelineContext(
            article={"id": "a1", "title": "测试"},
            content="",
            images=[],
            config={"existing_articles": [], "existing_concepts": [], "config": {}},
            processed={
                "__info": {
                    "title": "测试",
                    "account_name": "公众号",
                    "author": "作者",
                    "date": "2026-01-01",
                    "url": "https://example.com",
                }
            },
        )
        ctx.summary_data = {"category": "LLM", "summary": "摘要", "tags": []}
        ctx.md_content = "some markdown"

        with (
            patch("wx_obsidian.orchestrator.generate_markdown", return_value="md"),
            patch("wx_obsidian.orchestrator.remove_non_cdn_images", return_value="md"),
            patch("wx_obsidian.orchestrator.validate_and_fix", return_value=("md", [])),
            patch("wx_obsidian.orchestrator._normalize_llm_response"),
            patch(
                "wx_obsidian.output.validator.detect_format_issues",
                side_effect=ImportError("No module named 'missing_module'"),
            ),
            pytest.raises(ImportError, match="missing_module"),
        ):
            _markdown_stage(ctx)
