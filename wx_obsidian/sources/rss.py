"""文章抓取：WeWe RSS Feed + 微信文章 URL 内容提取。"""

from __future__ import annotations

import re
import threading
import time
from html.parser import HTMLParser
from typing import Any

import requests

from wx_obsidian.config import MAX_ARTICLE_LENGTH

# 全局锁：微信 URL 抓取串行化，避免并发触发反爬
_fetch_lock = threading.Lock()

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
    with _fetch_lock:
        time.sleep(1)  # 串行化 + 间隔，避免并发触发微信反爬
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
        return parser.get_text()[:MAX_ARTICLE_LENGTH], body_html
    except requests.RequestException as e:
        print(f"  抓取正文失败: {e}")
        return "", ""


def fetch_articles(config: dict[str, Any]) -> list[dict[str, Any]]:
    """从 WeWe RSS JSON Feed 获取文章列表。"""
    base_url = config["wewe_rss"]["base_url"]
    resp = requests.get(f"{base_url}/feeds/all.json", timeout=15)
    resp.raise_for_status()
    feed = resp.json()

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
                "date_published": item.get("date_published", ""),
                "author": author_name,
                "_account_name": author_name or "未知",
            }
        )

    return all_articles
