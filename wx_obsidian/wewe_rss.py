"""WeWe RSS API 客户端：账号管理、公众号管理、登录保活。"""

from __future__ import annotations

import json
import time
from typing import Any

import requests

from wx_obsidian.models import AccountStatus, Feed


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
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, list) and result:
            return result[0]
        return result

    # -- 账号状态 ------------------------------------------------------------

    def get_account_status(self) -> AccountStatus:
        """获取微信读书登录状态。"""
        result = self._trpc_call("account.list")
        data = result.get("result", {}).get("data", result)
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
        data = result.get("result", {}).get("data", result)
        items = data if isinstance(data, list) else data.get("items", data.get("list", []))
        feeds: list[Feed] = []
        for item in items:
            feeds.append(
                Feed(
                    id=str(item.get("id", "")),
                    name=item.get("mpName", item.get("name", "")),
                    intro=item.get("mpIntro", item.get("intro", "")),
                    cover=item.get("mpCover", item.get("cover", "")),
                )
            )
        return feeds

    def add_feed(self, article_url: str) -> Feed | None:
        """通过文章链接添加公众号。先获取公众号信息，再添加订阅。"""
        # Step 1: 通过文章链接获取公众号元信息
        mp_info = self._trpc_mutation("platform.getMpInfo", {"wxsLink": article_url})
        data = mp_info.get("result", {}).get("data", mp_info)
        if not data.get("id"):
            return None

        # Step 2: 添加订阅
        feed_data = {
            "id": data["id"],
            "mpName": data.get("mpName", ""),
            "mpCover": data.get("mpCover", ""),
            "mpIntro": data.get("mpIntro", ""),
            "updateTime": data.get("updateTime", int(time.time())),
        }
        result = self._trpc_mutation("feed.add", feed_data)
        feed_data_resp = result.get("result", {}).get("data", result)
        return Feed(
            id=str(feed_data_resp.get("id", data["id"])),
            name=feed_data_resp.get("mpName", data.get("mpName", "")),
            intro=feed_data_resp.get("mpIntro", data.get("mpIntro", "")),
            cover=feed_data_resp.get("mpCover", data.get("mpCover", "")),
        )

    def delete_feed(self, feed_id: str) -> bool:
        """删除公众号。成功返回 True，失败返回 False。"""
        try:
            self._trpc_mutation("feed.delete", feed_id)
            return True
        except requests.RequestException:
            return False

    # -- 登录保活 ------------------------------------------------------------

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
