"""WeWe RSS API 客户端：账号管理、公众号管理、登录保活。"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

from wx_obsidian.models import AccountStatus, Feed

logger = logging.getLogger(__name__)


class WeWeRSSClient:
    """WeWe RSS tRPC API 封装。"""

    def __init__(self, base_url: str, auth_code: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_code = auth_code

    # -- tRPC 调用 -----------------------------------------------------------

    def _trpc_call(self, procedure: str, data: dict[str, Any] | None = None) -> Any:
        """调用 WeWe RSS tRPC query 端点（GET）。"""
        url = f"{self._base_url}/trpc/{procedure}"
        params: dict[str, str] = {"input": json.dumps(data if data is not None else {})}
        resp = requests.get(
            url,
            params=params,
            headers={"Authorization": self._auth_code},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _trpc_mutation(self, procedure: str, data: Any = None) -> Any:
        """调用 WeWe RSS tRPC mutation 端点（POST batch 格式）。"""
        url = f"{self._base_url}/trpc/{procedure}"
        resp = requests.post(
            url,
            params={"batch": "1"},
            json={"0": data if data is not None else {}},
            headers={"Authorization": self._auth_code},
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
        logger.debug("tRPC %s response: %s", procedure, result)
        # tRPC batch 响应通常是 list，取第一个元素
        if isinstance(result, list) and result:
            return result[0]
        return result

    # -- 账号状态 ------------------------------------------------------------

    def get_account_status(self) -> AccountStatus:
        """获取微信读书登录状态。"""
        result = self._trpc_call("account.list")
        data = self._extract_trpc_data(result)
        if not isinstance(data, dict):
            return AccountStatus(is_logged_in=False)
        items = data.get("items", [])
        if not items:
            return AccountStatus(is_logged_in=False)
        account = items[0]
        return AccountStatus(
            is_logged_in=bool(account.get("status", 0) == 1),
            username=account.get("name"),
            need_refresh=False,
        )

    def is_healthy(self) -> bool:
        """WeWe RSS 服务是否可达。"""
        try:
            resp = requests.get(f"{self._base_url}/feeds/all.json", timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # -- 公众号管理 ----------------------------------------------------------

    def get_feeds(self) -> list[Feed]:
        """获取已添加的公众号列表。"""
        result = self._trpc_call("feed.list")
        data = self._extract_trpc_data(result)
        if not data:
            return []
        if isinstance(data, list):
            items: list[Any] = data
        elif isinstance(data, dict):
            items = data.get("items") or data.get("list") or data.get("feeds") or []
            # 如果 dict 本身看起来像单个 feed（有 mpName），包装成 list
            if not items and (data.get("mpName") or data.get("name")):
                items = [data]
        else:
            items = []
        feeds: list[Feed] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            feeds.append(
                Feed(
                    id=str(item.get("id") or item.get("mpId") or ""),
                    name=str(item.get("mpName") or item.get("name") or item.get("mp_name") or ""),
                    intro=str(item.get("mpIntro") or item.get("intro") or ""),
                    cover=str(item.get("mpCover") or item.get("cover") or ""),
                )
            )
        return feeds

    def _extract_trpc_data(self, raw: Any) -> Any:
        """从 tRPC 响应中提取 data 字段，兼容多种结构。

        WeWe RSS tRPC 响应结构示例:
          [{"result": {"data": {...}}}]          — mutation batch
          {"result": {"data": [...]}}             — query
          {"result": {"data": {"id": ...}}}]      — getMpInfo (data 是 dict)
        """
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if isinstance(raw, dict):
            result = raw.get("result", raw)
            if isinstance(result, dict):
                data = result.get("data", result)
                return data
        return raw

    def add_feed(self, article_url: str) -> Feed | None:
        """通过文章链接添加公众号。先获取公众号信息，再添加订阅。

        Returns:
            Feed 对象，或 None（链接无效时）。

        Raises:
            requests.RequestException: WeWe RSS 服务连接失败。
        """
        # Step 1: 通过文章链接获取公众号元信息
        try:
            mp_info = self._trpc_mutation("platform.getMpInfo", {"wxsLink": article_url})
        except requests.RequestException as exc:
            logger.error("连接 WeWe RSS 失败: %s", exc)
            raise
        data = self._extract_trpc_data(mp_info)
        if not isinstance(data, dict) or not data.get("id"):
            logger.warning(
                "getMpInfo 返回无效数据 (link=%s): raw=%s, extracted=%s",
                article_url,
                mp_info,
                data,
            )
            return None

        # Step 2: 添加订阅（getMpInfo 返回 name/cover/intro，feed.add 需要 mpName/mpCover/mpIntro）
        feed_data = {
            "id": data["id"],
            "mpName": data.get("mpName", data.get("name", "")),
            "mpCover": data.get("mpCover", data.get("cover", "")),
            "mpIntro": data.get("mpIntro", data.get("intro", "")),
            "updateTime": data.get("updateTime", int(time.time())),
        }
        try:
            result = self._trpc_mutation("feed.add", feed_data)
        except requests.RequestException as exc:
            logger.error("添加订阅失败: %s", exc)
            raise
        feed_data_resp = self._extract_trpc_data(result)
        if not isinstance(feed_data_resp, dict):
            feed_data_resp = data
        return Feed(
            id=str(feed_data_resp.get("id", data["id"])),
            name=str(
                feed_data_resp.get("mpName")
                or feed_data_resp.get("name")
                or data.get("mpName")
                or data.get("name")
                or ""
            ),
            intro=str(
                feed_data_resp.get("mpIntro")
                or feed_data_resp.get("intro")
                or data.get("mpIntro")
                or data.get("intro")
                or ""
            ),
            cover=str(
                feed_data_resp.get("mpCover")
                or feed_data_resp.get("cover")
                or data.get("mpCover")
                or data.get("cover")
                or ""
            ),
        )

    def delete_feed(self, feed_id: str) -> bool:
        """删除公众号。成功返回 True，失败返回 False。"""
        try:
            self._trpc_mutation("feed.delete", feed_id)
            return True
        except requests.RequestException:
            return False

    # -- 登录保活 ------------------------------------------------------------

    # -- 文章列表 ------------------------------------------------------------

    def get_articles(self, limit: int = 0) -> list[dict[str, Any]]:
        """通过 tRPC article.list 获取文章列表（含真实 publishTime）。

        Args:
            limit: 最大获取篇数，0 表示不限制（获取全部）。

        Returns:
            与 sources.rss.fetch_articles() 兼容的文章 dict 列表。
        """
        all_articles: list[dict[str, Any]] = []
        cursor: str | None = None
        page_size = 200

        while True:
            data: dict[str, Any] = {"limit": page_size}
            if cursor:
                data["cursor"] = cursor

            try:
                result = self._trpc_call("article.list", data)
            except requests.RequestException as e:
                logger.error("tRPC article.list 失败: %s", e)
                break

            raw = self._extract_trpc_data(result)
            if not isinstance(raw, dict):
                break

            items = raw.get("items", [])
            if not items:
                break

            for item in items:
                publish_ts = item.get("publishTime", 0)
                if publish_ts:
                    dt = datetime.fromtimestamp(publish_ts, tz=timezone.utc)
                    date_published = dt.isoformat().replace("+00:00", "Z")
                else:
                    date_published = ""

                article_id = item.get("id", "")
                mp_id = item.get("mpId") or item.get("mp_id") or item.get("feedId") or ""

                all_articles.append(
                    {
                        "id": article_id,
                        "title": item.get("title", "无标题"),
                        "url": f"https://mp.weixin.qq.com/s/{article_id}",
                        "content": "",
                        "date_published": date_published,
                        "author": "",
                        "_account_name": "",
                        "_mp_id": mp_id,
                    }
                )

            cursor = raw.get("nextCursor")
            if not cursor:
                break

            if limit > 0 and len(all_articles) >= limit:
                all_articles = all_articles[:limit]
                break

        # 通过 mpId 关联 feed 列表，填充 author 和 _account_name
        mp_name_map: dict[str, str] = {}
        try:
            feeds = self.get_feeds()
            for feed in feeds:
                mp_name_map[feed.id] = feed.name
            if not mp_name_map:
                logger.warning("feed 列表为空，所有文章的 source/author 将为空")
            else:
                matched = sum(1 for a in all_articles if mp_name_map.get(a.get("_mp_id", "")))
                logger.info(
                    "feed 映射: %d 个 feed, %d/%d 篇文章匹配到作者",
                    len(mp_name_map),
                    matched,
                    len(all_articles),
                )
        except (requests.RequestException, ValueError):
            logger.warning("获取 feed 列表失败，source/author 将为空")

        for article in all_articles:
            name = mp_name_map.get(article.get("_mp_id", ""), "")
            article["author"] = name
            article["_account_name"] = name

        return all_articles

    def refresh_cookie(self) -> bool:
        """刷新微信读书 cookie（访问 weread.qq.com 续期）。"""
        try:
            resp = requests.get("https://weread.qq.com", timeout=10)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    def get_login_url(self) -> str:
        """获取 WeWe RSS 登录页面 URL，引导用户在浏览器中扫码。"""
        return f"{self._base_url}/dash"
