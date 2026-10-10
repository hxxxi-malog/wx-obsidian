"""sources/we_mp_rss.py 单元测试：Feed 解析、ID 归一化、时间规范化、分页与降级。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

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
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": "MP_WXS_2399148061",
                        "mp_name": "腾讯研究院",
                        "mp_intro": "简介",
                        "mp_cover": "http://x/cover.jpg",
                    }
                ],
                "total": 1,
            },
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("wx_obsidian.sources.we_mp_rss.requests.get", return_value=mock_resp):
            feeds = client.list_feeds()

        assert len(feeds) == 1
        assert feeds[0].id == "MP_WXS_2399148061"
        assert feeds[0].name == "腾讯研究院"
        assert feeds[0].cover == "http://x/cover.jpg"

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
    def test_processed_ids_are_subset_of_legacy(self) -> None:
        """processed.json 的 363 条必须全部落在存量库中（历史兼容性实证）。"""
        legacy_ids = {article["id"] for article in self._legacy()["articles"]}
        processed = {
            key
            for key, value in json.loads(_PROCESSED.read_text(encoding="utf-8")).items()
            if isinstance(value, dict)
        }
        assert processed <= legacy_ids
        assert len(processed) == 363
