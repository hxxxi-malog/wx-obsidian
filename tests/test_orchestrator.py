"""orchestrator.py 单元测试。

架构变更后（WeWe RSS → ArticleSource 抽象 + 内联错误处理）：
- `Orchestrator.__init__(config_manager, source)`，source 为 ArticleSource。
- 健康状态字段 `wewe_rss` 更名为 `source`（HealthStatus）。
- `ErrorHandler` 已删除；重试/中止策略内联在编排流程中，因此这里的
  重试用例直接驱动 `Orchestrator`（见 TestFetchAndProcessRetryPolicy）。
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wx_obsidian.models import AccountStatus, ConnectionTestResult, Feed
from wx_obsidian.orchestrator import Orchestrator, _normalize_llm_response

#: 用于区分「字段缺失」与「字段为 None」
_MISSING = object()

# ---------------------------------------------------------------------------
# 测试替身与工具
# ---------------------------------------------------------------------------


class _StubConfigManager:
    """按 key 返回配置值的 ConfigManager 替身（MagicMock 无法区分 get 的 key）。"""

    def __init__(self, vault_path: Path) -> None:
        self._values: dict[str, Any] = {
            "obsidian.vault_path": str(vault_path),
            "obsidian.articles_dir": "公众号文章",
            "fetch.max_days": 7,
            "fetch.max_workers": 1,
        }
        self.save_count = 0

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value

    def save(self) -> None:
        self.save_count += 1

    def ensure_env_loaded(self) -> None:
        pass

    def resolve_llm_channel(self) -> dict[str, Any]:
        return {}

    def resolve_vision_channel(self) -> dict[str, Any]:
        return {}


def _make_orchestrator() -> tuple[Orchestrator, MagicMock, MagicMock]:
    """构造 Orchestrator：配置管理器与数据源均为可断言的 MagicMock。"""
    config_manager = MagicMock()
    source = MagicMock()
    return Orchestrator(config_manager, source), config_manager, source


def _article(article_id: str, date_published: str = "") -> dict[str, Any]:
    """构造数据源契约中的文章 dict。"""
    return {
        "id": article_id,
        "title": f"文章{article_id}",
        "url": f"https://mp.weixin.qq.com/s/{article_id}",
        "content": "",
        "date_published": date_published or datetime.now().strftime("%Y-%m-%d"),
        "_account_name": "测试公众号",
    }


def _drive_fetch_and_process(
    orch: Orchestrator,
    articles: list[dict[str, Any]],
    process_func: Any,
    processed: dict[str, Any],
) -> tuple[list[Any], MagicMock]:
    """驱动 _fetch_and_process_sync，屏蔽 vault 写入与网络副作用。

    Returns:
        (ProcessingResult 列表, save_processed mock)
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch("wx_obsidian.orchestrator.load_processed", return_value=processed)
        )
        mock_save = stack.enter_context(patch("wx_obsidian.orchestrator.save_processed"))
        stack.enter_context(patch("wx_obsidian.orchestrator.fetch_articles", return_value=articles))
        stack.enter_context(
            patch("wx_obsidian.orchestrator.scan_existing_content", return_value=([], []))
        )
        stack.enter_context(
            patch("wx_obsidian.orchestrator._process_single", side_effect=process_func)
        )
        stack.enter_context(patch("wx_obsidian.orchestrator._update_related_topics"))
        stack.enter_context(patch("wx_obsidian.orchestrator._update_knowledge_graph"))
        results = orch._fetch_and_process_sync()
    return results, mock_save


# ---------------------------------------------------------------------------
# 数据源代理
# ---------------------------------------------------------------------------


class TestArticleSourceDelegation:
    """Orchestrator 把数据源能力代理给构造时注入的 ArticleSource。"""

    def test_delegates_source_capabilities(self) -> None:
        orch, _cm, source = _make_orchestrator()
        account = AccountStatus(is_logged_in=True, username="tester")
        feed = Feed(id="f1", name="测试公众号")
        source.get_account_status.return_value = account
        source.is_healthy.return_value = True
        source.get_feeds.return_value = [feed]
        source.add_feed.return_value = Feed(id="f2", name="新公众号")
        source.delete_feed.return_value = True
        source.refresh_cookie.return_value = True
        source.get_login_url.return_value = "https://example.com/login"

        assert orch.get_account_status() is account
        assert orch.is_source_healthy() is True
        assert orch.get_feeds() == [feed]
        assert orch.add_feed("https://mp.weixin.qq.com/s/abc") is not None
        source.add_feed.assert_called_once_with("https://mp.weixin.qq.com/s/abc")
        assert orch.delete_feed("f1") is True
        source.delete_feed.assert_called_once_with("f1")
        assert orch.refresh_cookie() is True
        assert orch.get_login_url() == "https://example.com/login"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestGetHealthStatus:
    """Orchestrator.get_health_status 测试。"""

    def test_all_healthy(self, tmp_path: Path) -> None:
        orch, cm, _source = _make_orchestrator()
        source_ok = ConnectionTestResult(success=True, latency_ms=5, message="ok")
        llm_ok = ConnectionTestResult(success=True, latency_ms=10, message="ok")
        vision_ok = ConnectionTestResult(success=True, latency_ms=20, message="ok")
        cm.test_source_connection.return_value = source_ok
        cm.test_llm_connection.return_value = llm_ok
        cm.test_vision_connection.return_value = vision_ok
        cm.get.return_value = str(tmp_path)

        status = orch.get_health_status()

        assert status.overall is True
        # 字段由 wewe_rss 更名为 source
        assert status.source is source_ok
        assert status.vault_path is not None
        assert status.vault_path.success is True

    def test_source_down(self, tmp_path: Path) -> None:
        orch, cm, _source = _make_orchestrator()
        source_down = ConnectionTestResult(success=False, latency_ms=0, message="down")
        ok = ConnectionTestResult(success=True, latency_ms=10, message="ok")
        cm.test_source_connection.return_value = source_down
        cm.test_llm_connection.return_value = ok
        cm.test_vision_connection.return_value = ok
        cm.get.return_value = str(tmp_path)

        status = orch.get_health_status()

        assert status.overall is False
        assert status.source.success is False

    def test_no_vault_path(self) -> None:
        orch, cm, _source = _make_orchestrator()
        ok = ConnectionTestResult(success=True, latency_ms=10, message="ok")
        cm.test_source_connection.return_value = ok
        cm.test_llm_connection.return_value = ok
        cm.test_vision_connection.return_value = ok
        cm.get.return_value = ""

        status = orch.get_health_status()

        assert status.vault_path is None
        assert status.overall is True

    def test_vault_path_missing(self, tmp_path: Path) -> None:
        orch, cm, _source = _make_orchestrator()
        ok = ConnectionTestResult(success=True, latency_ms=10, message="ok")
        cm.test_source_connection.return_value = ok
        cm.test_llm_connection.return_value = ok
        cm.test_vision_connection.return_value = ok
        cm.get.return_value = str(tmp_path / "no_such_vault")

        status = orch.get_health_status()

        assert status.vault_path is not None
        assert status.vault_path.success is False
        assert status.overall is False


class TestGetStatistics:
    """Orchestrator.get_statistics 测试。"""

    def test_normal_statistics(self) -> None:
        orch, _cm, _source = _make_orchestrator()
        processed = {
            "a1": {"status": "done", "category": "LLM"},
            "a2": {"status": "done", "category": "LLM"},
            "a3": {"status": "error"},
            "a4": {"status": "skipped"},
        }
        with patch("wx_obsidian.orchestrator.load_processed", return_value=processed):
            stats = orch.get_statistics()
        assert stats.processed_articles == 2
        assert stats.failed_articles == 1
        assert stats.total_articles == 3
        assert stats.categories == {"LLM": 2}

    def test_empty_statistics(self) -> None:
        orch, _cm, _source = _make_orchestrator()
        with patch("wx_obsidian.orchestrator.load_processed", return_value={}):
            stats = orch.get_statistics()
        assert stats.total_articles == 0
        assert stats.processed_articles == 0


class TestGetFailedArticles:
    """Orchestrator.get_failed_articles 测试。"""

    def test_with_failed_file(self, tmp_path: Path) -> None:
        orch, _cm, _source = _make_orchestrator()
        failed_data = [
            {"article_id": "a1", "title": "失败文章", "error": "timeout", "retry_count": 2},
        ]
        failed_file = tmp_path / "failed.json"
        failed_file.write_text(json.dumps(failed_data), encoding="utf-8")

        with patch("wx_obsidian.config_manager.FAILED_FILE", failed_file):
            articles = orch.get_failed_articles()
        assert len(articles) == 1
        assert articles[0].article_id == "a1"
        assert articles[0].retry_count == 2

    def test_no_failed_file(self, tmp_path: Path) -> None:
        orch, _cm, _source = _make_orchestrator()
        missing = tmp_path / "no_such_file.json"
        with patch("wx_obsidian.config_manager.FAILED_FILE", missing):
            assert orch.get_failed_articles() == []

    def test_corrupted_failed_file(self, tmp_path: Path) -> None:
        orch, _cm, _source = _make_orchestrator()
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{broken", encoding="utf-8")
        with patch("wx_obsidian.config_manager.FAILED_FILE", bad_file):
            assert orch.get_failed_articles() == []


class TestRetryFailed:
    """Orchestrator.retry_failed 的重试/中止策略。"""

    @pytest.mark.asyncio
    async def test_clears_error_records_and_refetches(self, tmp_path: Path) -> None:
        orch, _cm, _source = _make_orchestrator()
        processed: dict[str, Any] = {
            "a1": {"status": "done", "category": "LLM"},
            "a2": {"status": "error", "title": "失败文章", "reason": "timeout"},
        }
        failed_file = tmp_path / "failed.json"

        with (
            patch("wx_obsidian.orchestrator.load_processed", return_value=processed),
            patch("wx_obsidian.orchestrator.save_processed") as mock_save,
            patch("wx_obsidian.config_manager.FAILED_FILE", failed_file),
            patch.object(orch, "fetch_and_process", new=AsyncMock(return_value=[])) as mock_fetch,
        ):
            results = await orch.retry_failed(["a2"])

        assert results == []
        saved_data = mock_save.call_args[0][0]
        # error 记录必须先清除，重新抓取才会认为该文章是新的
        assert "a2" not in saved_data
        assert "a1" in saved_data
        # 重抓篇数受清除条数限制，不会顺带处理其他新文章
        mock_fetch.assert_awaited_once_with(limit=1)
        # 原始错误信息落盘，便于事后排查
        archived = json.loads(failed_file.read_text(encoding="utf-8"))
        assert archived[0]["article_id"] == "a2"
        assert archived[0]["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_non_error_records_abort_before_refetch(self, tmp_path: Path) -> None:
        orch, _cm, _source = _make_orchestrator()
        processed: dict[str, Any] = {
            "a1": {"status": "done"},
            "a2": {"status": "skipped", "reason": "no_content"},
        }
        failed_file = tmp_path / "failed.json"

        with (
            patch("wx_obsidian.orchestrator.load_processed", return_value=processed),
            patch("wx_obsidian.orchestrator.save_processed") as mock_save,
            patch("wx_obsidian.config_manager.FAILED_FILE", failed_file),
            patch.object(orch, "fetch_and_process", new=AsyncMock()) as mock_fetch,
        ):
            results = await orch.retry_failed(["a1", "a2"])

        assert results == []
        # 只有 error 记录可重试；done/skipped 记录保持不变
        assert processed == {
            "a1": {"status": "done"},
            "a2": {"status": "skipped", "reason": "no_content"},
        }
        mock_save.assert_not_called()
        mock_fetch.assert_not_awaited()
        assert not failed_file.exists()


class TestFetchAndProcessRetryPolicy:
    """编排流程内联的重试/中止策略（原 ErrorHandler.with_retry 的替代）。"""

    def test_failed_article_auto_retried_within_run(self, tmp_path: Path) -> None:
        cm = _StubConfigManager(tmp_path)
        orch = Orchestrator(cm, MagicMock())
        attempts: list[str] = []

        def process(article: dict[str, Any], *_args: Any) -> dict[str, Any]:
            attempts.append(str(article["id"]))
            if len(attempts) == 1:
                return {
                    "article_id": "a1",
                    "title": "文章a1",
                    "status": "error",
                    "reason": "timeout",
                }
            return {"article_id": "a1", "title": "文章a1", "status": "done", "category": "LLM"}

        results, _mock_save = _drive_fetch_and_process(orch, [_article("a1")], process, {})

        assert attempts == ["a1", "a1"], "首次失败的文章应在同一次运行内自动重试"
        assert [r.status for r in results] == ["done"]
        assert results[0].article_id == "a1"
        assert cm.save_count == 1, "处理结束后应持久化配置（可能新增分类）"

    def test_persistent_failure_counts_attempts_and_stops(self, tmp_path: Path) -> None:
        orch = Orchestrator(_StubConfigManager(tmp_path), MagicMock())
        attempts: list[str] = []

        def process(article: dict[str, Any], *_args: Any) -> dict[str, Any]:
            attempts.append(str(article["id"]))
            return {
                "article_id": "a1",
                "title": "文章a1",
                "status": "error",
                "reason": "timeout",
            }

        results, mock_save = _drive_fetch_and_process(orch, [_article("a1")], process, {})

        assert len(attempts) == 2, "失败只自动重试一次，不会无限重试"
        assert [r.status for r in results] == ["error"]
        # 跨运行重试计数随失败递增
        assert mock_save.call_args[0][0]["a1"]["retry_count"] == 2

    def test_cross_run_retry_limit_skips_article(self, tmp_path: Path) -> None:
        orch = Orchestrator(_StubConfigManager(tmp_path), MagicMock())
        calls: list[str] = []

        def process(article: dict[str, Any], *_args: Any) -> dict[str, Any]:
            calls.append(str(article["id"]))
            return {"article_id": "a1", "title": "文章a1", "status": "error", "reason": "x"}

        processed: dict[str, Any] = {
            "a1": {"status": "error", "reason": "timeout", "retry_count": 3},
        }
        results, mock_save = _drive_fetch_and_process(orch, [_article("a1")], process, processed)

        assert results == []
        assert calls == [], "retry_count 达到上限的文章不应再进入处理"
        mock_save.assert_not_called()

    def test_stale_article_aborted_by_date_window(self, tmp_path: Path) -> None:
        orch = Orchestrator(_StubConfigManager(tmp_path), MagicMock())
        calls: list[str] = []

        def process(article: dict[str, Any], *_args: Any) -> dict[str, Any]:
            calls.append(str(article["id"]))
            return {"article_id": "a1", "title": "文章a1", "status": "done"}

        old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        results, _mock_save = _drive_fetch_and_process(
            orch, [_article("a1", old_date)], process, {}
        )

        assert results == []
        assert calls == [], "超出 fetch.max_days 的文章不应处理"


class TestNormalizeSubTopic:
    """sub_topic 归一化接入点：theme 词形统一，避免同主题分裂出多个二级目录。"""

    def _payload(self, sub_topic: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "tags": [],
            "concepts": [],
            "body_sections": [],
        }
        if sub_topic is not _MISSING:
            data["sub_topic"] = sub_topic
        return data

    def test_normalizes_alias(self) -> None:
        data = self._payload("Loop工程")
        _normalize_llm_response(data)
        assert data["sub_topic"] == "Loop Engineering"

    def test_keeps_unknown_topic(self) -> None:
        """归一化不能妨碍新增主题。"""
        data = self._payload("某个全新主题")
        _normalize_llm_response(data)
        assert data["sub_topic"] == "某个全新主题"

    def test_keeps_canonical_untouched(self) -> None:
        data = self._payload("多Agent协同")
        _normalize_llm_response(data)
        assert data["sub_topic"] == "多Agent协同"

    def test_non_string_reset_to_empty(self) -> None:
        data = self._payload(["Loop工程"])
        _normalize_llm_response(data)
        assert data["sub_topic"] == ""

    def test_missing_field_is_not_invented(self) -> None:
        data = self._payload(_MISSING)
        _normalize_llm_response(data)
        assert "sub_topic" not in data
