"""sources/we_mp_rss.py 单元测试：Feed 解析、ID 归一化、时间规范化、分页与降级。"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from wx_obsidian.sources.base import SourceAuthError
from wx_obsidian.sources.we_mp_rss import (
    FALLBACK_ID_PREFIX,
    WeMpRssClient,
    normalize_article_id,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_DATA = (
    _PROJECT_ROOT / "docs" / "design-docs" / "wewe-rss-replacement" / "legacy-wewe-data.json"
)
_PROCESSED = Path.home() / ".wx-obsidian" / "processed.json"


def _response(payload: dict[str, Any]) -> MagicMock:
    """把一段 JSON 包成 requests 响应 mock（默认 HTTP 200）。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = payload
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def _feed_list_payload(*rows: dict[str, Any]) -> dict[str, Any]:
    """构造 /api/v1/wx/mps 的列表响应（上游真实形态不带时间戳）。"""
    return {"code": 0, "data": {"list": list(rows), "total": len(rows)}}


def _feed_detail_payload(feed_id: str, sync_time: Any = 0, update_time: Any = 0) -> dict[str, Any]:
    """构造 /api/v1/wx/mps/{id} 的详情响应（时间戳为 Unix 秒）。"""
    return {
        "code": 0,
        "data": {
            "id": feed_id,
            "sync_time": sync_time,
            "update_time": update_time,
        },
    }


def _route(
    list_payload: dict[str, Any], details: dict[str, dict[str, Any]]
) -> Callable[..., MagicMock]:
    """按 URL 分流列表与详情请求。"""

    def _get(url: str, **_kwargs: Any) -> MagicMock:
        if url.endswith("/mps"):
            return _response(list_payload)
        feed_id = url.rsplit("/", 1)[-1]
        if feed_id not in details:
            raise AssertionError(f"未预期的详情请求: {url}")
        return _response(details[feed_id])

    return _get


def _feed_payload(items: list[dict[str, Any]], name: str = "聚合订阅") -> dict[str, Any]:
    """构造 /feed/{id}.json 的最小响应。"""
    return {"name": name, "items": items}


def _item(**overrides: Any) -> dict[str, Any]:
    """构造单条 item，默认值贴近上游真实结构。"""
    base: dict[str, Any] = {
        "id": "123",
        "title": "示例标题",
        "link": "https://mp.weixin.qq.com/s/QFwQz-Kqh9t_zssBU_a6Mg",
        "content": '<p>正文</p><img data-src="https://mmbiz.qpic.cn/a.png">',
        "updated": "2026-06-05T09:00:00+08:00",
        "channel_name": "腾讯技术工程",
    }
    base.update(overrides)
    return base


@pytest.fixture
def client() -> WeMpRssClient:
    return WeMpRssClient("http://localhost:8001", timeout=5)


# ---------------------------------------------------------------------------
# normalize_article_id
# ---------------------------------------------------------------------------


class TestNormalizeArticleId:
    """文章 ID 归一化：必须与 processed.json / wewe-rss 的 22 字符体系一致。"""

    def test_short_link(self) -> None:
        """标准短链取 /s/ 后的 token。"""
        link = "https://mp.weixin.qq.com/s/QFwQz-Kqh9t_zssBU_a6Mg"
        assert normalize_article_id(link) == "QFwQz-Kqh9t_zssBU_a6Mg"

    def test_token_with_tilde(self) -> None:
        """token 含 `~` 时不能被截断。

        2026-10-10 真实数据发现：微信短链 token 会出现波浪号，
        早期字符集缺 `~` 会截断 ID（旧库存量数据恰好没有 ~，离线回归覆盖不到）。
        """
        assert normalize_article_id("https://mp.weixin.qq.com/s/8moKQtvrKlMmIqqPQrs~Qw") == (
            "8moKQtvrKlMmIqqPQrs~Qw"
        )
        assert normalize_article_id("https://mp.weixin.qq.com/s/I~Fntev0Pkgko67xg5kJ-g") == (
            "I~Fntev0Pkgko67xg5kJ-g"
        )

    def test_long_link_uses_sn(self) -> None:
        """长链取 sn 参数。"""
        link = "https://mp.weixin.qq.com/s?__biz=MzA5&mid=1&idx=2&sn=abc123DEF456"
        assert normalize_article_id(link) == "abc123DEF456"

    def test_long_link_with_extra_params(self) -> None:
        """长链带其它查询参数时仍能取到 sn。"""
        link = "https://mp.weixin.qq.com/s?scene=1&sn=xyz789_ABC&chksm=deadbeef"
        assert normalize_article_id(link) == "xyz789_ABC"

    def test_unparsable_link_uses_fallback(self) -> None:
        """无法解析的链接退化为带前缀的兜底 ID。"""
        assert (
            normalize_article_id("https://example.com/a", fallback="42")
            == f"{FALLBACK_ID_PREFIX}42"
        )

    def test_empty_link_uses_fallback(self) -> None:
        """空链接同样走兜底。"""
        assert normalize_article_id("", fallback="42") == f"{FALLBACK_ID_PREFIX}42"

    def test_nothing_available_returns_empty(self) -> None:
        """既无链接也无兜底时返回空串，由调用方过滤。"""
        assert normalize_article_id("") == ""
        assert normalize_article_id("https://example.com/a") == ""

    def test_fallback_not_used_when_link_parses(self) -> None:
        """链接可解析时忽略兜底 ID。"""
        link = "https://mp.weixin.qq.com/s/QFwQz-Kqh9t_zssBU_a6Mg"
        assert normalize_article_id(link, fallback="999") == "QFwQz-Kqh9t_zssBU_a6Mg"


# ---------------------------------------------------------------------------
# get_articles
# ---------------------------------------------------------------------------


class TestGetArticles:
    """聚合 Feed 拉取与契约映射。"""

    def test_maps_item_to_contract(self, client: WeMpRssClient) -> None:
        """item 映射为本项目统一的文章 dict。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([_item()])
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            articles = client.get_articles(limit=10)

        assert len(articles) == 1
        article = articles[0]
        assert article["id"] == "QFwQz-Kqh9t_zssBU_a6Mg"
        assert article["title"] == "示例标题"
        assert article["url"] == "https://mp.weixin.qq.com/s/QFwQz-Kqh9t_zssBU_a6Mg"
        assert "mmbiz.qpic.cn" in article["content"]
        assert article["date_published"] == "2026-06-05T09:00:00+08:00"
        assert article["author"] == "腾讯技术工程"
        assert article["_account_name"] == "腾讯技术工程"

    def test_reads_only_no_gather(self, client: WeMpRssClient) -> None:
        """必须显式传 is_update=false，避免触发上游采集。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([_item()])
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            client.get_articles(limit=10)

        assert mock_get.call_args.kwargs["params"]["is_update"] == "false"

    def test_channel_name_falls_back_to_nested_feed(self, client: WeMpRssClient) -> None:
        """channel_name 缺失时回退到嵌套 feed.name。"""
        item = _item(channel_name="", feed={"id": "MP_WXS_1", "name": "架构师"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([item])
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            articles = client.get_articles(limit=10)

        assert articles[0]["_account_name"] == "架构师"

    def test_falls_back_to_top_level_feed_name(self, client: WeMpRssClient) -> None:
        """逐号请求时用顶层 feed.name。"""
        item = _item(channel_name="")
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([item], name="腾讯研究院")
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            articles = client.get_articles(limit=10, feed_id="MP_WXS_2399148061")

        assert articles[0]["author"] == "腾讯研究院"

    def test_skips_items_without_resolvable_id(self, client: WeMpRssClient) -> None:
        """无法归一化 ID 的条目被丢弃，避免污染 processed.json。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([_item(link="", id=""), _item()])
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            articles = client.get_articles(limit=10)

        assert len(articles) == 1

    def test_missing_items_returns_empty(self, client: WeMpRssClient) -> None:
        """响应没有 items 字段时返回空列表。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"name": "x"}
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert client.get_articles(limit=10) == []

    def test_non_json_response_returns_empty(self, client: WeMpRssClient) -> None:
        """非 JSON 响应降级为空列表而非抛错。"""
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("not json")
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert client.get_articles(limit=10) == []

    def test_paginates_until_limit(self, client: WeMpRssClient) -> None:
        """超过单页上限时自动翻页。"""
        pages: list[dict[str, Any]] = []

        def make_resp(*args: Any, **kwargs: Any) -> MagicMock:
            offset = kwargs["params"]["offset"]
            size = kwargs["params"]["limit"]
            pages.append(kwargs["params"])
            items = [
                _item(link=f"https://mp.weixin.qq.com/s/token{offset + i:04d}") for i in range(size)
            ]
            resp = MagicMock()
            resp.json.return_value = _feed_payload(items)
            resp.raise_for_status = MagicMock()
            return resp

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", side_effect=make_resp):
            articles = client.get_articles(limit=250)

        assert len(articles) == 250
        assert [p["offset"] for p in pages] == [0, 100, 200]
        assert [p["limit"] for p in pages] == [100, 100, 50]

    def test_stops_on_short_page(self, client: WeMpRssClient) -> None:
        """返回不足一页时立即停止翻页。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([_item()])
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            articles = client.get_articles(limit=1000)

        assert len(articles) == 1
        assert mock_get.call_count == 1

    def test_network_error_propagates(self, client: WeMpRssClient) -> None:
        """网络错误向上抛出，由 orchestrator 统一处理。"""
        with (
            patch(
                "wx_obsidian.sources.we_mp_rss.requests.get",
                side_effect=requests.RequestException("boom"),
            ),
            pytest.raises(requests.RequestException),
        ):
            client.get_articles(limit=10)

    def test_timeout_is_passed(self, client: WeMpRssClient) -> None:
        """外部调用必须带 timeout。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = _feed_payload([])
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            client.get_articles(limit=10)

        assert mock_get.call_args.kwargs["timeout"] == 5


# ---------------------------------------------------------------------------
# is_healthy
# ---------------------------------------------------------------------------


class TestIsHealthy:
    """健康检查。"""

    def test_healthy(self, client: WeMpRssClient) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert client.is_healthy() is True

    def test_unhealthy_status(self, client: WeMpRssClient) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert client.is_healthy() is False

    def test_network_error(self, client: WeMpRssClient) -> None:
        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get",
            side_effect=requests.RequestException("boom"),
        ):
            assert client.is_healthy() is False


# ---------------------------------------------------------------------------
# list_feeds
# ---------------------------------------------------------------------------


class TestListFeeds:
    """订阅列表读取。"""

    def test_normal_list(self, client: WeMpRssClient) -> None:
        """列表字段 + 详情补上的同步/更新时间。"""
        list_payload = _feed_list_payload(
            {
                "id": "MP_WXS_2399148061",
                "mp_name": "腾讯研究院",
                "mp_intro": "简介",
                "mp_cover": "http://x/cover.jpg",
            }
        )
        detail = _feed_detail_payload(
            "MP_WXS_2399148061", sync_time=1791652021, update_time=1791626400
        )

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get",
            side_effect=_route(list_payload, {"MP_WXS_2399148061": detail}),
        ):
            feeds = client.list_feeds()

        assert len(feeds) == 1
        assert feeds[0].id == "MP_WXS_2399148061"
        assert feeds[0].name == "腾讯研究院"
        assert feeds[0].cover == "http://x/cover.jpg"
        assert feeds[0].sync_time == datetime(2026, 10, 10, 17, 7, 1, tzinfo=UTC)
        assert feeds[0].update_time == datetime(2026, 10, 10, 10, 0, 0, tzinfo=UTC)

    def test_list_timestamps_skip_detail_requests(self, client: WeMpRssClient) -> None:
        """列表若已带时间戳（上游补全后的形态），不再逐号请求详情。"""
        list_payload = _feed_list_payload(
            {
                "id": "MP_WXS_2399148061",
                "mp_name": "腾讯研究院",
                "sync_time": 1791652021,
                "update_time": 1791626400,
            }
        )
        mock_get = MagicMock()
        mock_get.return_value = _response(list_payload)

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", mock_get):
            feeds = client.list_feeds()

        assert feeds[0].sync_time == datetime(2026, 10, 10, 17, 7, 1, tzinfo=UTC)
        assert mock_get.call_count == 1

    def test_zero_timestamp_means_never(self, client: WeMpRssClient) -> None:
        """上游用 0 表示「还没有过」，不能显示成 1970 年。"""
        list_payload = _feed_list_payload({"id": "MP_WXS_1", "mp_name": "新订阅"})
        detail = _feed_detail_payload("MP_WXS_1", sync_time=0, update_time=None)

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get",
            side_effect=_route(list_payload, {"MP_WXS_1": detail}),
        ):
            feeds = client.list_feeds()

        assert feeds[0].sync_time is None
        assert feeds[0].update_time is None

    def test_detail_error_propagates(self, client: WeMpRssClient) -> None:
        """详情接口失败要抛出来：界面报错可重试，不能静默显示「—」。"""
        list_payload = _feed_list_payload({"id": "MP_WXS_1", "mp_name": "某号"})
        failure = MagicMock()
        failure.json.return_value = {"code": 50001, "message": "获取公众号详情失败"}
        failure.raise_for_status.side_effect = requests.HTTPError("500")

        def _get(url: str, **_kwargs: Any) -> MagicMock:
            return _response(list_payload) if url.endswith("/mps") else failure

        with (
            patch("wx_obsidian.sources.we_mp_rss.requests.get", side_effect=_get),
            pytest.raises(requests.HTTPError),
        ):
            client.list_feeds()

    def test_detail_missing_data_raises(self, client: WeMpRssClient) -> None:
        """详情响应缺 data 时当错误处理，避免把结构异常显示成「没同步过」。"""
        list_payload = _feed_list_payload({"id": "MP_WXS_1", "mp_name": "某号"})
        broken = _response({"code": 0})

        def _get(url: str, **_kwargs: Any) -> MagicMock:
            return _response(list_payload) if url.endswith("/mps") else broken

        with (
            patch("wx_obsidian.sources.we_mp_rss.requests.get", side_effect=_get),
            pytest.raises(ValueError, match="详情结构异常"),
        ):
            client.list_feeds()

    def test_missing_data_returns_empty(self, client: WeMpRssClient) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 401, "message": "unauthorized"}
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert client.list_feeds() == []

    def test_network_error_returns_empty(self, client: WeMpRssClient) -> None:
        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get",
            side_effect=requests.RequestException("boom"),
        ):
            assert client.list_feeds() == []

    def test_access_key_sets_header(self) -> None:
        """配置 AK 时带上 AK-SK 授权头。"""
        client = WeMpRssClient("http://localhost:8001", timeout=5, access_key="ak-demo")
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"list": []}}
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            client.list_feeds()

        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "AK-SK ak-demo"

    def test_token_takes_precedence_over_access_key(self) -> None:
        """Bearer Token 优先于 AK（真实部署验证过的登录方式）。"""
        client = WeMpRssClient(
            "http://localhost:8001", timeout=5, access_key="ak-demo", token="tok-123"
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"list": []}}
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            client.list_feeds()

        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer tok-123"

    def test_no_credentials_sends_no_auth_header(self) -> None:
        """无凭据时不发送 Authorization。"""
        client = WeMpRssClient("http://localhost:8001", timeout=5)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"list": []}}
        mock_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            client.list_feeds()

        assert "Authorization" not in mock_get.call_args.kwargs["headers"]


# ---------------------------------------------------------------------------
# 离线回归：用恢复出的存量数据验证 ID 归一化
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _LEGACY_DATA.exists(), reason="缺少存量数据（docs 目录未随仓库分发）")
class TestLegacyIdRegression:
    """用旧 wewe-rss 库恢复的 1888 条记录验证归一化规则。"""

    def _legacy(self) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(_LEGACY_DATA.read_text(encoding="utf-8"))
        return data

    def test_all_legacy_ids_round_trip(self) -> None:
        """每篇文章的 22 字符 ID 都能从其原文链接归一化还原。"""
        data = self._legacy()
        articles = data["articles"]
        assert len(articles) > 1000

        mismatched = [
            article["id"]
            for article in articles
            if normalize_article_id(f"https://mp.weixin.qq.com/s/{article['id']}") != article["id"]
        ]
        assert mismatched == []

    def test_feeds_use_mp_wxs_book_ids(self) -> None:
        """订阅 ID 必须与微信读书 bookId 同体系，才能直接导入。"""
        feeds = self._legacy()["feeds"]
        assert len(feeds) == 29
        assert all(feed_id.startswith("MP_WXS_") for feed_id in feeds)
        assert all(info["name"] for info in feeds.values())

    @pytest.mark.skipif(not _PROCESSED.exists(), reason="缺少 processed.json")
    def test_historical_processed_ids_are_subset_of_legacy(self) -> None:
        """历史已处理的 ID 必须落在存量库中（ID 归一化兼容性实证）。

        计数沿革：2026-10-10 迁移时 363/363；2026-10-11 账本因事故重建后为
        352/360（少掉的 8 条是 7 条失败记录与笔记已不在 vault 的记录——它们**应该**
        被重新处理，所以不算回归）。下限取 340 是为了拦住「账本整段丢失」这类事故，
        而不是追求精确值：管线只增不减地处理新文章，但新文章 ID 天然不在旧库里。

        注意：这里**不能**断言 processed.json 的总条数或「是存量库子集」——
        迁移上线后管线会持续处理全新文章（其 ID 天然不在旧库中），
        存量库里也还有 1525 篇尚未处理的文章随时可能被补处理。
        因此断言「交集不少于回归下限」，并对新增 ID 校验格式。
        """
        legacy_ids = {article["id"] for article in self._legacy()["articles"]}
        processed = {
            key
            for key, value in json.loads(_PROCESSED.read_text(encoding="utf-8")).items()
            if isinstance(value, dict)
        }
        if len(processed) < 50:
            pytest.skip(f"processed.json 只有 {len(processed)} 条，像是测试数据而非真实账本")
        overlap = processed & legacy_ids
        assert len(overlap) >= 340, f"存量库命中的历史 ID 过少，实际 {len(overlap)}"

        # 迁移后新增的 ID 也必须是合法的 22 字符 token
        new_ids = processed - legacy_ids
        assert all(re.fullmatch(r"[A-Za-z0-9_~-]{22}", i) for i in new_ids)


# ---------------------------------------------------------------------------
# get_account_status
# ---------------------------------------------------------------------------


class TestGetAccountStatus:
    """登录态读取：必须能区分「没登录」与「查不到」。"""

    def _client(self, **kwargs: Any) -> WeMpRssClient:
        return WeMpRssClient(base_url="http://localhost:8001", timeout=5, **kwargs)

    def test_logged_in_uses_vid_when_name_empty(self) -> None:
        """上游的 name 实测常为空串，此时用 vid 兜底，免得界面显示「—」。"""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"has_cookie": True, "name": "", "vid": 399397397},
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            status = self._client(token="t").get_account_status()

        assert status.queried is True
        assert status.is_logged_in is True
        assert status.username == "vid 399397397"
        assert status.need_refresh is False

    def test_unauthorized_is_not_reported_as_logged_out(self) -> None:
        """401（未配令牌）时 queried=False：这是「查不到」，不是「未登录」。"""
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Client Error")

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            status = self._client(token="").get_account_status()

        assert status.is_logged_in is False
        assert status.queried is False

    def test_real_logged_out_is_queried(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"has_cookie": False, "vid": 1}}
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            status = self._client(token="t").get_account_status()

        assert status.queried is True
        assert status.is_logged_in is False
        assert status.need_refresh is True


# ---------------------------------------------------------------------------
# 凭据探针 / 扫码登录 / 单号采集
# ---------------------------------------------------------------------------


class TestProbeAccount:
    """探针要真的去上游验一次，且把失败原因分类（前端只有 expired 才弹二维码）。"""

    def _client(self) -> WeMpRssClient:
        return WeMpRssClient(base_url="http://localhost:8001", timeout=5, token="t")

    def test_valid(self) -> None:
        mock_resp = _response(
            {"code": 0, "data": {"mp_id": "MP_WXS_1", "mp_name": "腾讯研究院", "article_count": 1}}
        )
        with patch("wx_obsidian.sources.we_mp_rss.requests.post", return_value=mock_resp):
            probe = self._client().probe_account()

        assert probe.state == "valid"
        assert probe.feed_name == "腾讯研究院"
        assert probe.checked_at is not None

    def test_expired_cookie(self) -> None:
        """上游把微信读书的 401 包在业务错误里返回（HTTP 仍是 200）。"""
        mock_resp = _response(
            {
                "code": 400,
                "message": "WeRead MP API error 401: mp cover returned HTTP 401",
                "data": {"code": 401, "retriable": True},
            }
        )
        with patch("wx_obsidian.sources.we_mp_rss.requests.post", return_value=mock_resp):
            probe = self._client().probe_account()

        assert probe.state == "expired"
        assert "401" in probe.message

    def test_empty_cover_still_means_credential_ok(self) -> None:
        """能拿到「该号暂无文章」说明凭据是通的，不能当作失效。"""
        mock_resp = _response(
            {
                "code": 400,
                "message": "WeRead MP API error empty_cover: 公众号未返回最新文章",
                "data": {"code": "empty_cover", "retriable": False},
            }
        )
        with patch("wx_obsidian.sources.we_mp_rss.requests.post", return_value=mock_resp):
            probe = self._client().probe_account()

        assert probe.state == "valid"

    def test_upstream_token_rejected(self) -> None:
        mock_resp = _response({"detail": "invalid token"})
        mock_resp.status_code = 401
        with patch("wx_obsidian.sources.we_mp_rss.requests.post", return_value=mock_resp):
            probe = self._client().probe_account()

        assert probe.state == "token_invalid"

    def test_unreachable(self) -> None:
        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.post",
            side_effect=requests.RequestException("boom"),
        ):
            probe = self._client().probe_account()

        assert probe.state == "unreachable"

    def test_other_failure_is_unknown(self) -> None:
        mock_resp = _response({"code": 400, "message": "公众号不存在", "data": {"code": 40401}})
        with patch("wx_obsidian.sources.we_mp_rss.requests.post", return_value=mock_resp):
            probe = self._client().probe_account()

        assert probe.state == "unknown"
        assert "公众号不存在" in probe.message


class TestLoginQr:
    """扫码登录：二维码转 data URL、状态透传、收尾。"""

    def _client(self) -> WeMpRssClient:
        return WeMpRssClient(base_url="http://localhost:8001", timeout=5, token="t")

    def test_code_is_downloaded_and_encoded(self) -> None:
        code_resp = _response(
            {"code": 0, "data": {"code": "/static/weread_qrcode.png", "uid": "uid-1"}}
        )
        image_resp = MagicMock()
        image_resp.content = b"\x89PNG\r\n\x1a\n"
        image_resp.raise_for_status = MagicMock()

        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get",
            side_effect=[code_resp, image_resp],
        ) as mock_get:
            payload = self._client().get_login_qr()

        assert payload["image"].startswith("data:image/png;base64,")
        assert payload["uid"] == "uid-1"
        assert mock_get.call_args_list[1].args[0].endswith("/static/weread_qrcode.png")

    def test_missing_code_raises(self) -> None:
        with (
            patch(
                "wx_obsidian.sources.we_mp_rss.requests.get",
                return_value=_response({"code": 500, "message": "获取二维码失败，请稍后重试"}),
            ),
            pytest.raises(ValueError, match="二维码"),
        ):
            self._client().get_login_qr()

    def test_status_maps_logic_code(self) -> None:
        mock_resp = _response(
            {
                "code": 0,
                "data": {
                    "login_status": False,
                    "msg": "登录成功但 Cookie 验证失败",
                    "data": {"logicCode": "COOKIE_INVALID"},
                },
            }
        )
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            status = self._client().get_login_qr_status()

        assert status["login_status"] is False
        assert status["logic_code"] == "COOKIE_INVALID"
        assert "Cookie" in status["message"]

    def test_finish_reports_logged_in(self) -> None:
        mock_resp = _response({"code": 0, "data": {"vid": "399397397"}})
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            result = self._client().finish_login_qr()

        assert result["login_status"] is True

    def test_finish_reports_not_logged_in(self) -> None:
        mock_resp = _response({"code": 0, "data": {}})
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            result = self._client().finish_login_qr()

        assert result["login_status"] is False


class TestRefreshFeed:
    """触发单号采集：区分「已触发 / 太频繁 / 失败」。"""

    def _client(self) -> WeMpRssClient:
        return WeMpRssClient(base_url="http://localhost:8001", timeout=5, token="t")

    def test_triggered(self) -> None:
        mock_resp = _response({"code": 0, "data": {"time_span": 1000, "list": []}})
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert self._client().refresh_feed("MP_WXS_1") == "triggered"

    def test_too_frequent_is_skipped(self) -> None:
        mock_resp = _response({"code": 40402, "message": "请不要频繁更新操作"})
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert self._client().refresh_feed("MP_WXS_1") == "skipped"

    def test_other_error_is_failed(self) -> None:
        mock_resp = _response({"code": 40401, "message": "请选择一个公众号"})
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            assert self._client().refresh_feed("MP_WXS_1") == "failed"


class TestListArticles:
    """文章库读取：管理接口分页、ID/时间归一化、令牌失效的显式区分。"""

    def _client(self) -> WeMpRssClient:
        return WeMpRssClient(base_url="http://localhost:8001", timeout=5, token="tok-123")

    def _payload(self, *rows: dict[str, Any], total: int | None = None) -> dict[str, Any]:
        return {"code": 0, "data": {"list": list(rows), "total": total or len(rows)}}

    def _row(self, **overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": "3537616032-3537616032_wpHU8~UqRh-D-XYfzfZe1Q",
            "title": "LiteLLM 半年两度被投毒",
            "url": "https://mp.weixin.qq.com/s/wpHU8~UqRh-D-XYfzfZe1Q",
            "mp_name": "阿里云云原生",
            "mp_id": "MP_WXS_3537616032",
            "publish_time": 1792732008,
            "pic_url": "https://mmbiz.qpic.cn/cover.jpg",
            "has_content": 1,
        }
        base.update(overrides)
        return base

    def test_parses_page_and_sends_pagination(self) -> None:
        """字段归一化 + offset/limit/mp_id 透传 + Bearer 头。"""
        mock_resp = _response(self._payload(self._row(), total=54))
        with patch(
            "wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp
        ) as mock_get:
            page = self._client().list_articles(offset=30, limit=15, feed_id="MP_WXS_3537616032")

        assert page.total == 54
        assert page.offset == 30
        assert page.limit == 15
        assert len(page.items) == 1
        article = page.items[0]
        assert article.article_id == "wpHU8~UqRh-D-XYfzfZe1Q"
        assert article.title == "LiteLLM 半年两度被投毒"
        assert article.url == "https://mp.weixin.qq.com/s/wpHU8~UqRh-D-XYfzfZe1Q"
        assert article.account == "阿里云云原生"
        assert article.account_id == "MP_WXS_3537616032"
        assert article.published_at == datetime(2026, 10, 23, 5, 6, 48, tzinfo=UTC)
        assert article.cover == "https://mmbiz.qpic.cn/cover.jpg"
        assert article.has_content is True

        call = mock_get.call_args
        assert call.args[0] == "http://localhost:8001/api/v1/wx/articles"
        assert call.kwargs["params"] == {
            "offset": 30,
            "limit": 15,
            "mp_id": "MP_WXS_3537616032",
        }
        assert call.kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert call.kwargs["timeout"] == 5

    def test_omits_mp_id_when_not_filtering(self) -> None:
        """不筛公众号时不发空的 mp_id：上游按空串处理只会多一层误解。"""
        mock_get = MagicMock()
        mock_get.return_value = _response(self._payload())
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", mock_get):
            page = self._client().list_articles()

        assert page.items == []
        assert mock_get.call_args.kwargs["params"] == {"offset": 0, "limit": 30}

    def test_zero_publish_time_is_none(self) -> None:
        """上游用 0 表示「时间未知」，不能显示成 1970 年。"""
        mock_resp = _response(self._payload(self._row(publish_time=0)))
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            page = self._client().list_articles()

        assert page.items[0].published_at is None

    def test_url_fallback_when_link_unparsable(self) -> None:
        """链接解析不出 token 时退回带前缀的上游 id，与 get_articles 的 ID 约定一致。"""
        mock_resp = _response(self._payload(self._row(url="", id="internal-id-1")))
        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            page = self._client().list_articles()

        assert page.items[0].article_id == f"{FALLBACK_ID_PREFIX}internal-id-1"

    @pytest.mark.parametrize("status_code", [401, 403])
    def test_token_rejected_raises_auth_error(self, status_code: int) -> None:
        """401/403 单列成 SourceAuthError：界面要提示去配令牌，而不是重试网络。"""
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        with (
            patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp),
            pytest.raises(SourceAuthError),
        ):
            self._client().list_articles()
        mock_resp.raise_for_status.assert_not_called()

    def test_missing_data_raises(self) -> None:
        """响应缺 data.list 时按结构异常抛出，不能当成「没有文章」。"""
        mock_resp = _response({"code": 0})
        with (
            patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp),
            pytest.raises(ValueError, match="文章列表结构异常"),
        ):
            self._client().list_articles()

    def test_network_error_propagates(self) -> None:
        with (
            patch(
                "wx_obsidian.sources.we_mp_rss.requests.get",
                side_effect=requests.RequestException("boom"),
            ),
            pytest.raises(requests.RequestException),
        ):
            self._client().list_articles()

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [({"limit": 0}, "limit"), ({"limit": 101}, "limit"), ({"offset": -1}, "offset")],
    )
    def test_invalid_pagination_rejected_before_request(
        self, kwargs: dict[str, int], match: str
    ) -> None:
        """越界分页参数在本地就拒绝，不把非法请求发给上游。"""
        with (
            patch("wx_obsidian.sources.we_mp_rss.requests.get") as mock_get,
            pytest.raises(ValueError, match=match),
        ):
            self._client().list_articles(**kwargs)
        mock_get.assert_not_called()
