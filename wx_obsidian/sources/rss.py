"""文章抓取：数据源抽象（we-mp-rss）+ 微信文章 URL 内容提取。"""

from __future__ import annotations

import logging
import re
import threading
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

import requests

from wx_obsidian.config import MAX_ARTICLE_LENGTH

if TYPE_CHECKING:
    from wx_obsidian.sources.base import ArticleSource

logger = logging.getLogger(__name__)

# 速率限制：最多 2 个并发请求，请求间隔至少 0.5 秒
_fetch_semaphore = threading.Semaphore(2)
_fetch_last_time = 0.0
_fetch_time_lock = threading.Lock()
_FETCH_MIN_INTERVAL = 0.5

# 抓取窗口上限的兜底值（仅回退路径与未配置 fetch.feed_limit 时使用）。
# 注意：we-mp-rss 的 /feed/*.json 会连正文一起返回，limit 过大会显著放大传输量，
# 因此默认值由 config 的 fetch.feed_limit 控制（见 config_manager._DEFAULT_CONFIG）。
_FEED_LIMIT = 1000

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


def fetch_articles(
    config: dict[str, Any],
    source: ArticleSource,
) -> list[dict[str, Any]]:
    """通过数据源获取文章列表。

    Args:
        config: 配置字典；抓取窗口由 `fetch.feed_limit` 控制。
        source: ArticleSource 实现（由 sources.factory 构造）。

    Returns:
        统一契约的文章 dict 列表（字段一致性由各实现自行保证）。
        数据源不可用时返回空列表并记录错误，不再回退到已下线的旧源。
    """
    limit = _resolve_feed_limit(config)

    try:
        articles = source.get_articles(limit=limit)
    except (requests.RequestException, ValueError) as e:
        logger.error("数据源获取失败: %s", e)
        return []

    if not articles:
        logger.warning("数据源返回空列表")
        return []

    if len(articles) >= limit:
        logger.warning("文章数达到上限 %d，抓取窗口可能被截断；可调大 fetch.feed_limit", limit)
    logger.info("通过数据源获取 %d 篇文章", len(articles))
    return articles


def _resolve_feed_limit(config: dict[str, Any]) -> int:
    """抓取窗口大小；未配置或非法时退回 _FEED_LIMIT。"""
    fetch_config = config.get("fetch")
    if isinstance(fetch_config, dict):
        configured = fetch_config.get("feed_limit")
        if isinstance(configured, int) and configured > 0:
            return configured
    return _FEED_LIMIT
