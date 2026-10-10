"""Collect WeChat Official Account articles through WeRead Web."""

import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from core.log import logger
from core.models.feed import Feed
from core.print import print_info
from core.wx.model.weread import MpsWeread


WEREAD_WEB_BASE = "https://weread.qq.com"


class WereadMPAPIError(Exception):
    def __init__(self, code, message, retriable=True):
        super().__init__(f"WeRead MP API error {code}: {message}")
        self.code = code
        self.message = message
        self.retriable = retriable


def build_mp_url(original_id: str) -> str:
    original_id = str(original_id or "").strip()
    if not original_id:
        return ""
    # 注意：微信文章短链 token 中可能含 '~'（如 4OcS7~rrtk2Lwe4P0YPiGg），
    # 这是合法字符，必须原样保留。若替换为 '_'，微信会 302 跳转、部分阅读器无法打开。
    article_token = quote(original_id, safe="~")
    return f"https://mp.weixin.qq.com/s/{article_token}"


def build_mp_link_from_review_id(review_id: str, book_id: str = "") -> str:
    """从 WeRead 的 reviewId 推导公众号原文链接。

    reviewId 形如 ``MP_WXS_<bookId>_<articleToken>``，末尾段即为
    mp.weixin.qq.com 原文短链的 token。token 中可能含 ``~``（如
    ``4OcS7~rrtk2Lwe4P0YPiGg``），须原样保留。新版接口 ``/api/mp/cover``
    不返回 originalId，只能由此推导。
    """
    review_id = str(review_id or "").strip()
    if not review_id:
        return ""
    token = review_id
    prefix = f"{book_id}_" if book_id else ""
    if prefix and review_id.startswith(prefix):
        token = review_id[len(prefix):]
    elif "_" in token:
        token = token.split("_")[-1]
    return f"https://mp.weixin.qq.com/s/{quote(token, safe='~')}"


def _raise_response_error(payload: dict):
    code = payload.get("errCode", payload.get("errcode", 0))
    try:
        code = int(code or 0)
    except (TypeError, ValueError):
        code = 0
    if code == 0:
        return
    message = payload.get("errMsg") or payload.get("errmsg") or str(code)
    raise WereadMPAPIError(
        code,
        message,
        retriable=code not in (-2041, -2012, -2010),
    )


def parse_mp_articles(payload: dict):
    if not isinstance(payload, dict):
        raise WereadMPAPIError("invalid_response", "response is not an object")
    _raise_response_error(payload)

    groups = payload.get("reviews", []) or []
    articles = []
    for group in groups:
        group_time = group.get("createTime", 0)
        for sub_review in group.get("subReviews", []) or []:
            review = sub_review.get("review") or {}
            mp_info = review.get("mpInfo") or {}
            review_id = review.get("reviewId") or sub_review.get("reviewId")
            if not review_id:
                continue
            create_time = review.get("createTime") or group_time or 0
            update_time = mp_info.get("time") or create_time
            articles.append({
                "aid": review_id,
                "id": review_id,
                "title": mp_info.get("title", ""),
                "link": build_mp_url(mp_info.get("originalId", "")),
                "cover": mp_info.get("pic_url", ""),
                "digest": mp_info.get("content") or review.get("content", ""),
                "content": "",
                "create_time": create_time,
                "update_time": update_time,
                "read_num": mp_info.get("readNum", 0),
                "like_num": mp_info.get("likeNum", 0),
                "item_show_type": 0,
            })
    return articles, len(groups)


def extract_mp_content(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    content = soup.select_one("#js_content") or soup.select_one(".rich_media_content")
    if content is None:
        raise WereadMPAPIError("invalid_content", "article body was not found")
    for element in content.select("script, style"):
        element.decompose()
    return content.decode_contents().strip()


# [wx-obsidian patch 2026-10-10] 提取真实发布时间。
#
# 背景：`/web/mp/content` 返回的是完整页面（含外层 HTML，约 3.5MB），里面带有
# `var ct = "1791540000"` 形式的真实发布时间。上游只调用 extract_mp_content()
# 抠出 #js_content，把外层连同时间字段一起丢了，导致 cover 兜底模式只能拿
# `int(time.time())` 冒充发布时间（上游自己在 :335-336 的注释里也承认了这点）。
#
# 影响：wx-obsidian 侧会把「抓取时间」当成文章发布日期写入笔记 frontmatter，
# 并使 fetch.max_days 的日期过滤失效。
#
# 该页面在本路径下**本来就被请求**，因此本补丁不产生任何额外网络请求。
RE_PUBLISH_TIME = re.compile(r'var\s+ct\s*=\s*["\'](\d{9,})["\']')
RE_PUBLISH_TIME_ALT = re.compile(r'publish_time\s*=\s*["\'](\d{9,})["\']')


def extract_publish_time(html: str) -> int:
    """从微信文章/微信读书内容页中提取真实发布时间（Unix 秒）；取不到返回 0。"""
    for pattern in (RE_PUBLISH_TIME, RE_PUBLISH_TIME_ALT):
        matched = pattern.search(html or "")
        if matched:
            try:
                return int(matched.group(1))
            except (TypeError, ValueError):
                continue
    return 0


class MpsWereadMP(MpsWeread):
    """WeRead-backed collector for existing ``MP_WXS_*`` feeds."""

    def _get_feed_update_time(self, mp_id: str) -> int:
        from core.db import DB

        session = DB.get_session()
        try:
            row = session.query(Feed.update_time).filter(Feed.id == mp_id).first()
            return int(row[0] or 0) if row else 0
        finally:
            session.close()

    def _get_catchup_page_limit(self, requested_pages: int) -> int:
        from core.config import cfg

        configured_limit = int(cfg.get("weread.mp_max_pages", 20) or 20)
        return max(requested_pages, configured_limit, 1)

    def _get_content_interval(self) -> float:
        from core.config import cfg

        return max(float(cfg.get("weread.content_interval", 2) or 0), 0)

    def _get_page_interval(self) -> float:
        from core.config import cfg

        return max(float(cfg.get("weread.page_interval", 1) or 0), 0)

    def _request_headers(self, include_ticket=False):
        headers = {
            "Cookie": self._weread_cookies,
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://weread.qq.com",
            "Referer": "https://weread.qq.com/",
        }
        if include_ticket and self._weread_ticket:
            # 新版微信读书已弃用 x-wr-ticket，仅需有效 Cookie 即可拉取文章列表；
            # 保留旧逻辑以便兼容旧版微信读书。无 ticket 时不拦截请求。
            headers["x-wr-ticket"] = self._weread_ticket
        return headers

    def _get_mp_articles_page(self, book_id: str, offset=0):
        # 注意：/web/mp/articles 已被新版微信读书废弃（实测恒返回 -2041），
        # 该方法仅保留供旧版兼容与测试使用；生产链路请用 _get_mp_cover。
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/web/mp/articles",
                params={"bookId": book_id, "offset": offset},
                headers=self._request_headers(include_ticket=True),
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"article list returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WereadMPAPIError("invalid_json", "article list is not JSON") from exc
        if not isinstance(payload, dict):
            raise WereadMPAPIError("invalid_response", "article list is not an object")
        _raise_response_error(payload)
        return payload

    def _get_mp_cover(self, book_id: str) -> dict:
        """获取公众号最新一篇文章（新版微信读书唯一可用入口）。

        返回: {"name", "title", "pic", "reviewId", ...}；无文章或接口异常时抛错。
        """
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/api/mp/cover",
                params={"bookId": book_id},
                headers=self._request_headers(),
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"mp cover returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WereadMPAPIError("invalid_json", "mp cover is not JSON") from exc
        if not isinstance(payload, dict):
            raise WereadMPAPIError("invalid_response", "mp cover is not an object")
        if not payload.get("reviewId"):
            _raise_response_error(payload)
            raise WereadMPAPIError(
                "empty_cover",
                "公众号未返回最新文章（可能从未在微信读书内发布过文章）",
                retriable=False,
            )
        return payload

    def _is_article_gathered(self, mp_id: str, review_id: str) -> bool:
        """判断 reviewId 是否已入库（跨会话增量，避免重复推送）。

        Article.id 存储格式为 ``{mp_id}-{aid}`` 并去掉 ``MP_WXS_`` 前缀，
        与 ``Db.add_article`` 的归一化规则保持一致。
        """
        if not mp_id or not review_id:
            return False
        from core.db import DB
        from core.models.article import Article

        session = DB.get_session()
        try:
            stored_id = f"{mp_id}-{review_id}".replace("MP_WXS_", "")
            row = session.query(Article.id).filter(
                Article.mp_id == mp_id,
                Article.id == stored_id,
            ).first()
            return row is not None
        except Exception:
            return False
        finally:
            session.close()

    def _get_mp_content(self, review_id: str):
        headers = self._request_headers()
        headers["Accept"] = "text/html,application/xhtml+xml,*/*"
        try:
            response = requests.get(
                f"{WEREAD_WEB_BASE}/web/mp/content",
                params={"reviewId": review_id},
                headers=headers,
                proxies=self._get_proxies(),
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            raise WereadMPAPIError("network_error", str(exc)) from exc
        if response.status_code != 200:
            raise WereadMPAPIError(
                response.status_code,
                f"article content returned HTTP {response.status_code}",
            )
        # [wx-obsidian patch 2026-10-10] 顺带记录真实发布时间，供 cover 路径使用
        self._content_publish_time = extract_publish_time(response.text)
        return extract_mp_content(response.text)

    def get_Articles(
        self,
        faker_id: str = None,
        Mps_id: str = None,
        Mps_title="",
        CallBack=None,
        start_page: int = 0,
        MaxPage: int = 1,
        interval=10,
        Gather_Content=False,
        Item_Over_CallBack=None,
        Over_CallBack=None,
    ):
        """Collect one existing WeChat feed without changing its RSS identity.

        新版微信读书网页版已废弃 ``/web/mp/articles`` 列表接口（恒返回 -2041），
        前端仅保留 ``/api/mp/cover``（最新一篇）+ ``/web/mp/content``（正文）。
        因此采用「cover 增量」方案：每次只取最新一篇文章，已入库则跳过，
        无法回补历史文章列表（旧版接口才支持）。
        """
        self.articles = []
        self.aids = []
        self.get_token()
        self.start_time = time.time()
        self.response_valid = False
        self.last_error = None
        self._load_weread_auth()

        if not self._weread_cookies:
            raise WereadMPAPIError(
                "missing_cookie",
                "WEREAD_COOKIE is required",
                retriable=False,
            )

        book_id = Mps_id if str(Mps_id or "").startswith("MP_WXS_") else faker_id
        if not str(book_id or "").startswith("MP_WXS_"):
            raise WereadMPAPIError(
                "invalid_book_id",
                "the feed id must use the MP_WXS_ prefix",
                retriable=False,
            )

        gather_content = bool(Gather_Content or self.Gather_Content)
        content_interval = self._get_content_interval()
        latest_publish_time = 0

        print_info(f"微信读书公众号采集模式: {Mps_title} ({book_id})")
        try:
            cover = self._get_mp_cover(book_id)
            review_id = (cover.get("reviewId") or "").strip()
            title = (cover.get("title") or "").strip()
            self.response_valid = True

            if not review_id:
                raise WereadMPAPIError(
                    "empty_cover",
                    f"公众号「{Mps_title}」暂无文章",
                    retriable=False,
                )

            # 跨会话增量：最新文章已入库则无需重复采集
            if self._is_article_gathered(Mps_id, review_id):
                print_info(f"无新文章：最新《{title}》已在库中，跳过")
                latest_publish_time = self._get_feed_update_time(Mps_id)
            else:
                print_info(f"发现新文章: {title}")
                item = {
                    "aid": review_id,
                    "id": review_id,
                    "title": title,
                    "link": build_mp_link_from_review_id(review_id, book_id),
                    "cover": cover.get("pic") or "",
                    "digest": cover.get("digest") or "",
                    "content": "",
                    "create_time": int(time.time()),
                    "update_time": int(time.time()),
                    "read_num": 0,
                    "like_num": 0,
                    "item_show_type": 0,
                    "copyright_stat": 1,
                    "mp_id": Mps_id,
                }
                if gather_content:
                    if content_interval:
                        time.sleep(content_interval)
                    # [wx-obsidian patch 2026-10-10] 先清零，避免复用上一篇的时间
                    self._content_publish_time = 0
                    try:
                        item["content"] = self._get_mp_content(review_id)
                    except WereadMPAPIError as exc:
                        logger.warning(f"微信读书正文获取失败 [{review_id}]: {exc}")
                # [wx-obsidian patch 2026-10-10] 用正文页里的真实发布时间覆盖抓取时间。
                # 上游此处硬编码 int(time.time())，会让“发布时间”等于采集时刻，
                # 使下游无法按真实日期归档/过滤。取不到时保持原值（退化为旧行为）。
                real_publish_time = getattr(self, "_content_publish_time", 0)
                if real_publish_time:
                    item["create_time"] = real_publish_time
                    item["update_time"] = real_publish_time
                if CallBack is not None:
                    super().FillBack(
                        CallBack=CallBack,
                        data=item,
                        Ext_Data={"mp_title": Mps_title, "mp_id": Mps_id},
                    )
                if Item_Over_CallBack is not None:
                    Item_Over_CallBack(item)
                latest_publish_time = int(item["update_time"])

            self.update_mps(
                Mps_id,
                Feed(
                    sync_time=int(time.time()),
                    update_time=latest_publish_time or None,
                ),
            )
        except Exception as exc:
            self.last_error = {
                "message": str(exc),
                "code": getattr(exc, "code", "weread_mp_error"),
                "retriable": getattr(exc, "retriable", True),
            }
            super().Over(CallBack=Over_CallBack)
            raise

        super().Over(CallBack=Over_CallBack)
