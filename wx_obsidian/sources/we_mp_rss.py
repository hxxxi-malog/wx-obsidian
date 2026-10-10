"""we-mp-rss 数据源适配：聚合 JSON Feed 拉取、文章 ID 归一化、订阅列表读取。

we-mp-rss 为 WeWe RSS 的替代数据源（详见 docs/design-docs/wewe-rss-replacement/spec.md）。
本模块只负责「取数 + 归一化」，不依赖 processing/ 与 output/（AGENTS.md 红线 6）。

关键设计：
- 走 `GET /feed/{feed_id}.json`（公开、本地库读取、不触发上游抓取），
  其 `items[]` 同时携带全文 HTML（图片为直连 mmbiz）与公众号名，
  因此无需逐号请求即可拿到 `_account_name`。
- `is_update=false` 明确只读库、不触发采集。
- 文章 ID 归一化为 `mp.weixin.qq.com/s/<token>` 的 token，
  与 processed.json / 旧 wewe-rss 的 22 字符 ID 体系保持一致。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from wx_obsidian.models import Feed

logger = logging.getLogger(__name__)

# 上游 /feed/{feed_id}.json 的 limit 上限（Query(ge=1, le=100)）
PAGE_LIMIT = 100

# 无法从链接解析 token 时的 ID 前缀，避免与 22 字符短链 token 混淆
FALLBACK_ID_PREFIX = "wemprss_"

# 文章短链：https://mp.weixin.qq.com/s/<token>
#
# token 字符集必须包含 `~`：2026-10-10 用真实数据校验时发现确实存在含波浪号的 token
# （如 /s/8moKQtvrKlMmIqqPQrs~Qw），早期只用 [A-Za-z0-9_-] 会把 token 截断，
# 生成错误的 ID 从而破坏 processed.json 的去重。旧库存量数据恰好没有 `~`，
# 所以离线回归没有覆盖到这个分支。
RE_SHORT_LINK = re.compile(r"/s/([A-Za-z0-9_~-]{6,})")


def normalize_article_id(link: str, fallback: str = "") -> str:
    """把文章链接归一化为短链 token（与 processed.json / wewe-rss 的 ID 体系一致）。

    优先级：`/s/<token>` → 长链 `?__biz=..&sn=<sn>` → `wemprss_<fallback>` 兜底。

    Args:
        link: 文章链接，来自 we-mp-rss 的 `items[].link`。
        fallback: 链接不可解析时使用的兜底标识（通常是上游内部 id）。

    Returns:
        归一化后的文章 ID；无法归一化且无 fallback 时返回空串。
    """
    if link:
        matched = RE_SHORT_LINK.search(link)
        if matched:
            return matched.group(1)
        try:
            query = parse_qs(urlparse(link).query)
        except ValueError:
            query = {}
        sn = (query.get("sn") or [""])[0].strip()
        if sn:
            return sn

    if fallback:
        return f"{FALLBACK_ID_PREFIX}{fallback}"
    return ""


def _feed_name(item: dict[str, Any]) -> str:
    """从 item 的嵌套 `feed` 字段取公众号名。"""
    feed = item.get("feed")
    if isinstance(feed, dict):
        return str(feed.get("name") or "")
    return ""


def _to_iso8601(value: Any) -> str:
    """把上游 `updated` 字段（ISO 字符串或 Unix 时间戳）规范化为 ISO 8601。

    orchestrator._utc_to_beijing_date() 依赖 ISO 8601；直接喂 RFC822 或裸时间戳
    会被截成非法日期，故在此统一。
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        # Python 3.9 的 fromisoformat 不支持 Z 后缀，需手动替换
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("无法解析发布时间: %r", text)
        return text
    return parsed.isoformat()


class WeMpRssClient:
    """we-mp-rss HTTP 客户端（只读数据源）。"""

    def __init__(
        self,
        base_url: str,
        timeout: int = 15,
        access_key: str = "",
        token: str = "",
    ) -> None:
        """初始化客户端。

        Args:
            base_url: we-mp-rss 服务地址，如 http://localhost:8001。
            timeout: 单次请求超时（秒）。外部 API 调用必须显式设置。
            access_key: 可选 AK，以 `AK-SK <key>` 形式鉴权管理接口。
            token: 可选 Bearer Token（`POST /api/v1/wx/auth/token` 获取），
                优先级高于 access_key。仅 `/api/v1/wx/*` 管理接口需要；
                `/feed/*` 为公开只读接口，无需任何凭据。
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._access_key = access_key
        self._token = token

    # -- 内部方法 -----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """构造请求头。"""
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        elif self._access_key:
            headers["Authorization"] = f"AK-SK {self._access_key}"
        return headers

    def _fetch_feed_page(self, feed_id: str, limit: int, offset: int) -> dict[str, Any]:
        """拉取单页 Feed JSON。

        Raises:
            requests.RequestException: 网络失败或非 2xx 响应。
        """
        params: dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "is_update": "false",
        }
        response = requests.get(
            f"{self._base_url}/feed/{feed_id}.json",
            params=params,
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            logger.error("we-mp-rss 返回非 JSON 响应: %s", exc)
            return {}
        if not isinstance(payload, dict):
            logger.error("we-mp-rss 返回结构异常: %s", type(payload).__name__)
            return {}
        return payload

    def _to_article(self, item: Any, feed: dict[str, Any]) -> dict[str, Any]:
        """把上游 item 转为本项目统一的文章 dict。"""
        if not isinstance(item, dict):
            return {"id": ""}

        link = str(item.get("link") or "")
        article_id = normalize_article_id(link, fallback=str(item.get("id") or ""))
        channel = (
            str(item.get("channel_name") or "") or _feed_name(item) or str(feed.get("name") or "")
        )

        return {
            "id": article_id,
            "title": str(item.get("title") or "无标题"),
            "url": link,
            "content": str(item.get("content") or ""),
            "date_published": _to_iso8601(item.get("updated")),
            "author": channel,
            "_account_name": channel or "未知",
        }

    # -- 公开方法 -----------------------------------------------------------

    def is_healthy(self, feed_id: str = "all") -> bool:
        """服务是否可达且 Feed 接口可用。"""
        try:
            params: dict[str, Any] = {"limit": 1, "is_update": "false"}
            response = requests.get(
                f"{self._base_url}/feed/{feed_id}.json",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def get_articles(
        self,
        limit: int = 1000,
        feed_id: str = "all",
    ) -> list[dict[str, Any]]:
        """拉取文章列表（聚合源，按 limit 自动分页）。

        Args:
            limit: 最多返回篇数；上游单页上限 100，超出自动翻页。
            feed_id: `all` 为聚合源；也可传单个 `MP_WXS_*` 只取该号。

        Returns:
            与 `sources.rss.fetch_articles()` 兼容的文章 dict 列表。

        Raises:
            requests.RequestException: 网络失败或非 2xx 响应。
        """
        articles: list[dict[str, Any]] = []
        offset = 0

        while len(articles) < limit:
            page_size = min(PAGE_LIMIT, limit - len(articles))
            payload = self._fetch_feed_page(feed_id, page_size, offset)
            items = payload.get("items")
            if not isinstance(items, list) or not items:
                break

            for item in items:
                article = self._to_article(item, payload)
                if article["id"]:
                    articles.append(article)

            if len(items) < page_size:
                break
            offset += len(items)

        logger.info("we-mp-rss 拉取 %d 篇文章（feed_id=%s）", len(articles), feed_id)
        return articles[:limit]

    def list_feeds(self, limit: int = 100) -> list[Feed]:
        """读取 we-mp-rss 的订阅列表。

        上游路径为 `GET /api/v1/wx/mps`（prefix 来自 core/base.py:API_BASE='/api/v1/wx'），
        该接口需要 Bearer Token 或 AK，未配置凭据时会返回 401；此时返回空列表而非
        抛错，避免拖垮主流程。`/feed/*` 才是无需鉴权的公开接口。

        Args:
            limit: 最多返回条数（上游上限 100）。

        Returns:
            Feed 列表；不可用或无凭据时为空列表。
        """
        try:
            params: dict[str, Any] = {"limit": limit, "offset": 0}
            response = requests.get(
                f"{self._base_url}/api/v1/wx/mps",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("获取 we-mp-rss 订阅列表失败: %s", exc)
            return []

        if not isinstance(payload, dict):
            return []
        data = payload.get("data")
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            logger.warning("we-mp-rss 订阅列表结构异常，缺少 data.list")
            return []

        feeds: list[Feed] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            feeds.append(
                Feed(
                    id=str(row["id"]),
                    name=str(row.get("mp_name") or ""),
                    intro=str(row.get("mp_intro") or ""),
                    cover=str(row.get("mp_cover") or ""),
                )
            )
        return feeds
