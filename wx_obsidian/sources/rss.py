"""文章抓取：WeWe RSS Feed + 微信文章 URL 内容提取。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from html.parser import HTMLParser
from typing import Any

import requests

from wx_obsidian.config import MAX_ARTICLE_LENGTH

logger = logging.getLogger(__name__)

# 速率限制：最多 2 个并发请求，请求间隔至少 0.5 秒
_fetch_semaphore = threading.Semaphore(2)
_fetch_last_time = 0.0
_fetch_time_lock = threading.Lock()
_FETCH_MIN_INTERVAL = 0.5

# 预编译正则
RE_BODY_HTML = re.compile(r'id="js_content"[^>]*>(.*?)</div>\s*<script', re.DOTALL)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# HTML 文本提取
# ---------------------------------------------------------------------------


class HTMLTextExtractor(HTMLParser):
    """从 HTML 中提取纯文本内容。"""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr"):
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._text.append(data.strip())

    def get_text(self) -> str:
        """返回提取的纯文本。"""
        return "\n".join(line for line in "".join(self._text).splitlines() if line.strip())


# ---------------------------------------------------------------------------
# 内部公共函数
# ---------------------------------------------------------------------------


def _fetch_html(url: str) -> str:
    """从微信文章 URL 抓取正文 HTML。"""
    global _fetch_last_time
    with _fetch_semaphore:
        # 自适应延迟：确保请求间隔至少 _FETCH_MIN_INTERVAL 秒
        with _fetch_time_lock:
            now = time.monotonic()
            elapsed = now - _fetch_last_time
            if elapsed < _FETCH_MIN_INTERVAL:
                time.sleep(_FETCH_MIN_INTERVAL - elapsed)
            _fetch_last_time = time.monotonic()
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.encoding = "utf-8"
        match = RE_BODY_HTML.search(resp.text)
        return match.group(1) if match else resp.text


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def fetch_article_content_and_images(
    url: str,
) -> tuple[str, str]:
    """从微信文章 URL 抓取正文纯文本和 body HTML。

    Returns:
        (纯文本, body_html)。调用方负责从 body_html 中提取图片。
    """
    try:
        body_html = _fetch_html(url)
        parser = HTMLTextExtractor()
        parser.feed(body_html)
        text = parser.get_text()[:MAX_ARTICLE_LENGTH]
        if len(text) < 50:
            logger.warning("URL 抓取内容过短 (%d 字符): %s", len(text), url[:80])
        return text, body_html
    except requests.RequestException as e:
        logger.warning("URL 抓取失败: %s — %s", url[:80], e)
        return "", ""


def fetch_articles(config: dict[str, Any]) -> list[dict[str, Any]]:
    """从 WeWe RSS JSON Feed 获取文章列表。"""
    base_url = config["wewe_rss"]["base_url"]
    resp = requests.get(f"{base_url}/feeds/all.json", timeout=15)
    resp.raise_for_status()
    try:
        feed = resp.json()
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("RSS Feed 返回非 JSON 响应: %s", e)
        return []

    all_articles: list[dict[str, Any]] = []
    for item in feed.get("items", []):
        author_info = item.get("author", {})
        author_name = (
            author_info.get("name", "") if isinstance(author_info, dict) else str(author_info)
        )
        all_articles.append(
            {
                "id": item.get("id", item.get("url", "")),
                "title": item.get("title", "无标题"),
                "url": item.get("url", item.get("external_url", "")),
                "content": item.get("content_html", item.get("content_text", "")),
                "date_published": item.get("date_published") or item.get("date_modified", ""),
                "author": author_name,
                "_account_name": author_name or "未知",
            }
        )

    return all_articles
