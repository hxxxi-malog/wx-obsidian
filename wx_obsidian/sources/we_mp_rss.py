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
- 订阅列表接口 `GET /api/v1/wx/mps` 不返回 sync_time / update_time，只有
  单号详情接口返回；因此列表读完后按号补一次详情请求（并发，见
  `_fill_feed_times`），否则界面上「同步」「更新」永远是空的。
"""

from __future__ import annotations

import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from wx_obsidian.models import AccountProbe, AccountStatus, Feed, SourceArticle, SourceArticlePage
from wx_obsidian.sources.base import SourceAuthError

logger = logging.getLogger(__name__)

# 上游 /feed/{feed_id}.json 的 limit 上限（Query(ge=1, le=100)）
PAGE_LIMIT = 100

# 补订阅时间戳时并发请求详情的上限。29 个订阅实测：串行 113ms，8 并发 45ms。
FEED_DETAIL_WORKERS = 8

# 无法从链接解析 token 时的 ID 前缀，避免与 22 字符短链 token 混淆
FALLBACK_ID_PREFIX = "wemprss_"

# 凭据探针的状态（web 层原样透传给前端，前端据此决定要不要弹扫码登录）
PROBE_VALID = "valid"
PROBE_EXPIRED = "expired"
PROBE_TOKEN_INVALID = "token_invalid"
PROBE_UNREACHABLE = "unreachable"
PROBE_UNKNOWN = "unknown"

# 微信读书表示「登录态失效」的错误码：401 是 HTTP 状态，-2012 是服务端 errCode
_EXPIRED_CODES = {401, -2012}

# 单号「更新」被上游判为「过于频繁」时返回的业务码（sync_interval 内的重复请求）
FEED_REFRESH_TOO_FREQUENT = 40402

# 文章短链：https://mp.weixin.qq.com/s/<token>
#
# 上游给的 token 里会出现 `~`（如 /s/8moKQtvrKlMmIqqPQrs~Qw）：WeRead 用它代替
# token 内的 `_`（reviewId 用 `_` 分隔 bookId 与 token），而微信只认 `_` —— `~`
# 形式打开是「参数错误」页面。所以解析出 token 后要还原，见 restore_short_link。
RE_SHORT_LINK = re.compile(r"/s/([A-Za-z0-9_~-]{6,})")

SHORT_LINK_PREFIX = "https://mp.weixin.qq.com/s/"


def restore_short_link(link: str) -> str:
    """把短链 token 里的 `~` 还原成 `_`，返回微信能打开的地址。

    Args:
        link: 上游文章链接；长链（`/s?__biz=...`）、其它域名与空串原样返回。

    Returns:
        可直接在浏览器打开的地址。
    """
    if not link.startswith(SHORT_LINK_PREFIX):
        return link
    token, sep, query = link[len(SHORT_LINK_PREFIX) :].partition("?")
    return f"{SHORT_LINK_PREFIX}{token.replace('~', '_')}{sep}{query}"


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
        dt = datetime.fromtimestamp(float(value), tz=UTC)
        return dt.isoformat().replace("+00:00", "Z")

    text = str(value or "").strip()
    if not text:
        return ""
    try:
        # 底线是 3.12，fromisoformat 自 3.11 起原生支持 Z 后缀；这层 replace 是
        # 当年兼容 3.9 留下的，行为等价，故不动（日期解析没有直接单测兜底）。
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("无法解析发布时间: %r", text)
        return text
    return parsed.isoformat()


def _to_datetime(value: Any) -> datetime | None:
    """把上游的 Unix 秒时间戳转成 UTC 时间。

    0、负数、空值都表示「还没有过」，一律返回 None，避免界面上显示 1970 年。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC)


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

        link = restore_short_link(str(item.get("link") or ""))
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

    @staticmethod
    def _to_source_article(item: dict[str, Any]) -> SourceArticle:
        """把管理接口返回的文章行转成列表页 DTO。

        上游行里的 id 是内部复合 ID（`<mp_id>-<mp_id>_<token>`），这里用链接重新
        归一化成短链 token，才能和 processed.json 里的记录对上。链接里的 `~` 先还原
        成 `_`：归一化后的 ID 与用户在浏览器里打开的地址必须指向同一个 token。
        """
        url = restore_short_link(str(item.get("url") or ""))
        upstream_id = str(item.get("id") or "")
        return SourceArticle(
            article_id=normalize_article_id(url, fallback=upstream_id) or upstream_id,
            title=str(item.get("title") or "无标题"),
            url=url,
            account=str(item.get("mp_name") or ""),
            account_id=str(item.get("mp_id") or ""),
            published_at=_to_datetime(item.get("publish_time")),
            cover=str(item.get("pic_url") or ""),
            has_content=bool(item.get("has_content")),
        )

    def _fetch_feed_times(self, feed_id: str) -> tuple[datetime | None, datetime | None]:
        """读取单个公众号的（同步时间, 更新时间）。

        列表接口不返回这两列，只有 `GET /api/v1/wx/mps/{id}` 返回；本方法供
        `_fill_feed_times` 并发调用。

        Raises:
            requests.RequestException: 网络失败或非 2xx 响应。
            ValueError: 响应结构异常（缺少 data 对象）。
        """
        response = requests.get(
            f"{self._base_url}/api/v1/wx/mps/{feed_id}",
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ValueError(f"we-mp-rss 公众号详情结构异常（缺少 data）: {feed_id}")
        return _to_datetime(data.get("sync_time")), _to_datetime(data.get("update_time"))

    def _fill_feed_times(self, feeds: list[Feed]) -> None:
        """给缺时间的订阅补上同步/更新时间（并发请求详情接口）。"""
        pending = [feed for feed in feeds if feed.sync_time is None or feed.update_time is None]
        if not pending:
            return

        workers = min(FEED_DETAIL_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda feed: self._fetch_feed_times(feed.id), pending))

        for feed, (sync_time, update_time) in zip(pending, results, strict=True):
            feed.sync_time = sync_time
            feed.update_time = update_time

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

        列表接口不返回 sync_time / update_time（只有单号详情返回），因此这里读完后
        逐号补一次详情请求；补不到就保持 None，界面显示「—」。

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
                    sync_time=_to_datetime(row.get("sync_time")),
                    update_time=_to_datetime(row.get("update_time")),
                )
            )

        self._fill_feed_times(feeds)
        return feeds

    # -- ArticleSource 协议方法 ---------------------------------------------

    def get_feeds(self) -> list[Feed]:
        """已订阅的公众号列表（ArticleSource 协议名）。"""
        return self.list_feeds()

    def list_articles(
        self,
        *,
        offset: int = 0,
        limit: int = 30,
        feed_id: str = "",
    ) -> SourceArticlePage:
        """读取上游文章库（按发布时间倒序，支持按公众号过滤与分页）。

        走管理接口 `GET /api/v1/wx/articles`：只查本地库、不触发采集，返回的是
        `ArticleBase` 投影（不含正文），因此响应体远小于 `/feed/all.json`；
        同时带 `total`，界面才能显示总数并判断「加载更多」何时结束。

        Args:
            offset: 起始偏移。
            limit: 单页条数（上游上限 100）。
            feed_id: 只取单个公众号；空串表示全部订阅。

        Returns:
            分页结果（`total` 为上游总数，`items` 为当前页）。

        Raises:
            SourceAuthError: 访问令牌缺失或失效（上游 401/403）。
            requests.RequestException: 网络失败或非 2xx 响应。
            ValueError: 响应结构异常，或分页参数越界。
        """
        if not 1 <= limit <= PAGE_LIMIT:
            raise ValueError(f"limit 必须在 1..{PAGE_LIMIT} 之间，收到 {limit}")
        if offset < 0:
            raise ValueError(f"offset 不能为负数，收到 {offset}")

        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if feed_id:
            params["mp_id"] = feed_id

        response = requests.get(
            f"{self._base_url}/api/v1/wx/articles",
            params=params,
            headers=self._headers(),
            timeout=self._timeout,
        )
        if response.status_code in (401, 403):
            raise SourceAuthError("数据源访问令牌未配置或已失效（上游返回 401/403）")
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        rows = data.get("list") if isinstance(data, dict) else None
        if not isinstance(data, dict) or not isinstance(rows, list):
            raise ValueError("we-mp-rss 文章列表结构异常（缺少 data.list）")

        return SourceArticlePage(
            total=int(data.get("total") or 0),
            offset=offset,
            limit=limit,
            items=[self._to_source_article(row) for row in rows if isinstance(row, dict)],
        )

    def get_account_status(self) -> AccountStatus:
        """微信读书登录态。

        上游 `GET /api/v1/wx/weread` 需要 Bearer Token；未配置 token 或请求失败时
        拿不到任何数据，此时返回 `queried=False`（**不是**「未登录」），
        调用方据此区分「真的没登录」与「压根查不到」。
        """
        payload = self._get_json("/api/v1/wx/weread")
        data = payload.get("data")
        if not isinstance(data, dict):
            return AccountStatus(is_logged_in=False, queried=False)

        has_cookie = bool(data.get("has_cookie"))
        # 上游的 name 实测常为空串，vid 才是稳定的账号标识；用它兜底免得界面显示「—」
        name = str(data.get("name") or "").strip()
        vid = str(data.get("vid") or "").strip()
        return AccountStatus(
            is_logged_in=has_cookie,
            username=name or (f"vid {vid}" if vid else None),
            need_refresh=not has_cookie,
        )

    def add_feed(self, article_url: str) -> Feed | None:
        """we-mp-rss 不支持「按文章链接添加公众号」。

        旧源 WeWe RSS 有 `platform.getMpInfo(wxsLink)` 可按链接反查公众号，
        we-mp-rss 没有等价接口。请到 we-mp-rss 后台（http://127.0.0.1:8001）
        按名称订阅。此处返回 None 并告警，调用方需给出相应提示。
        """
        logger.warning(
            "we-mp-rss 数据源不支持按文章链接添加公众号（url=%s），请改用名称搜索订阅",
            article_url[:80],
        )
        return None

    def delete_feed(self, feed_id: str) -> bool:
        """取消订阅：`DELETE /api/v1/wx/mps/{feed_id}`。"""
        try:
            response = requests.delete(
                f"{self._base_url}/api/v1/wx/mps/{feed_id}",
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("取消订阅失败 (%s): %s", feed_id, exc)
            return False
        return True

    def refresh_cookie(self) -> bool:
        """探测数据源可用性。

        we-mp-rss 自行维护微信读书 Cookie（内置浏览器定时刷新），本项目不持有凭据，
        因此这里只反映服务可达性，不代表登录态是否有效。
        """
        return self.is_healthy()

    def get_login_url(self) -> str:
        """微信读书扫码授权页面。"""
        return f"{self._base_url}/weread"

    def probe_account(self) -> AccountProbe:
        """真实校验微信读书 Cookie 是否还有效。

        上游 `POST /api/v1/wx/weread/mp/test` 会拿 Cookie 请求一次公众号封面：
        200 + code=0 说明凭据可用。失败原因分四类，账号页据此决定弹不弹扫码窗
        （只有 `expired` 才弹；令牌或可达性问题弹窗会误导）。

        Returns:
            探针结果。任何失败都以 `state` 表达，不抛异常——页面需要显示原因。
        """
        checked_at = datetime.now(UTC)
        try:
            response = requests.post(
                f"{self._base_url}/api/v1/wx/weread/mp/test",
                json={},
                headers=self._headers(),
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            return AccountProbe(PROBE_UNREACHABLE, f"数据源连不上：{exc}", checked_at=checked_at)

        if response.status_code in (401, 403):
            return AccountProbe(
                PROBE_TOKEN_INVALID, "数据源访问令牌无效或已过期", checked_at=checked_at
            )
        if response.status_code != 200:
            return AccountProbe(
                PROBE_UNKNOWN, f"探针请求失败：HTTP {response.status_code}", checked_at=checked_at
            )
        try:
            payload = response.json()
        except ValueError:
            return AccountProbe(PROBE_UNKNOWN, "探针返回的不是 JSON", checked_at=checked_at)
        if not isinstance(payload, dict):
            return AccountProbe(PROBE_UNKNOWN, "探针返回结构异常", checked_at=checked_at)

        if payload.get("code") == 0:
            data = payload.get("data")
            feed_name = str(data.get("mp_name") or "") if isinstance(data, dict) else ""
            return AccountProbe(
                PROBE_VALID, "微信读书凭据有效", feed_name=feed_name, checked_at=checked_at
            )

        message = str(payload.get("message") or "")
        inner = payload.get("data")
        inner_code = inner.get("code") if isinstance(inner, dict) else None
        if inner_code in _EXPIRED_CODES or "401" in message or "-2012" in message:
            return AccountProbe(
                PROBE_EXPIRED, message or "微信读书登录态已失效", checked_at=checked_at
            )
        if inner_code == "empty_cover":
            # 能拿到「该号没有文章」，说明凭据本身是通的
            return AccountProbe(
                PROBE_VALID, "微信读书凭据有效（所选公众号暂无文章）", checked_at=checked_at
            )
        return AccountProbe(
            PROBE_UNKNOWN, message or f"探针返回异常：{payload}", checked_at=checked_at
        )

    def get_login_qr(self) -> dict[str, Any]:
        """取扫码登录二维码，转成 data URL（前端直接塞进 `<img>`）。

        Raises:
            ValueError: 上游没有返回二维码地址，或图片取不回来。
            requests.RequestException: 网络失败或非 2xx 响应。
        """
        payload = self._get_json("/api/v1/wx/weread/qr/code")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError(str(payload.get("message") or "上游没有返回二维码"))
        code = str(data.get("code") or "").strip()
        uid = str(data.get("uid") or "")
        if not code:
            raise ValueError("上游没有返回二维码地址")
        if code.startswith("data:"):
            return {"image": code, "uid": uid}

        url = code if code.startswith("http") else f"{self._base_url}/{code.lstrip('/')}"
        response = requests.get(url, timeout=self._timeout)
        response.raise_for_status()
        encoded = base64.b64encode(response.content).decode("ascii")
        return {"image": f"data:image/png;base64,{encoded}", "uid": uid}

    def get_login_qr_status(self) -> dict[str, Any]:
        """轮询扫码状态。返回 `{"login_status", "message", "logic_code"}`。"""
        payload = self._get_json("/api/v1/wx/weread/qr/status")
        data = payload.get("data")
        if not isinstance(data, dict):
            return {"login_status": False, "message": "读取扫码状态失败", "logic_code": ""}
        detail = data.get("data")
        logic_code = str(detail.get("logicCode") or "") if isinstance(detail, dict) else ""
        return {
            "login_status": bool(data.get("login_status")),
            "message": str(data.get("msg") or ""),
            "logic_code": logic_code,
        }

    def finish_login_qr(self) -> dict[str, Any]:
        """扫码完成后收尾（上游会停轮询、删二维码图）。"""
        payload = self._get_json("/api/v1/wx/weread/qr/over")
        data = payload.get("data")
        logged_in = bool(data)
        return {
            "login_status": logged_in,
            "message": "登录成功" if logged_in else "尚未完成扫码登录",
        }

    def refresh_feed(self, feed_id: str) -> str:
        """触发单个公众号的上游采集（上游自己起线程，本调用立即返回）。

        Returns:
            `triggered`（已触发）/ `skipped`（上游判定过于频繁）/ `failed`。
        """
        response = requests.get(
            f"{self._base_url}/api/v1/wx/mps/update/{feed_id}",
            headers=self._headers(),
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code") == 0:
            return "triggered"
        if isinstance(payload, dict) and payload.get("code") == FEED_REFRESH_TOO_FREQUENT:
            return "skipped"
        return "failed"

    # -- 内部请求封装 --------------------------------------------------------

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET 并解析 JSON；任何失败都返回空 dict（调用方按空值降级）。"""
        try:
            response = requests.get(
                f"{self._base_url}{path}",
                params=params,
                headers=self._headers(),
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("we-mp-rss 请求失败 %s: %s", path, exc)
            return {}
        return payload if isinstance(payload, dict) else {}
