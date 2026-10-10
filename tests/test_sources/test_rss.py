"""sources/rss.py 测试：HTML 正文提取、数据源取文契约。"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import requests

from wx_obsidian.sources.rss import HTMLTextExtractor, fetch_articles


class TestHTMLTextExtractor:
    """HTMLTextExtractor 测试。"""

    def test_extract_paragraphs(self):
        """提取段落文本。"""
        html = "<p>第一段</p><p>第二段</p>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "第一段" in text
        assert "第二段" in text

    def test_skip_script_tags(self):
        """跳过 script 标签内容。"""
        html = "<p>正文</p><script>var x = 1;</script><p>更多正文</p>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "正文" in text
        assert "var x" not in text

    def test_skip_style_tags(self):
        """跳过 style 标签内容。"""
        html = "<p>正文</p><style>.class { color: red; }</style><p>更多</p>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "正文" in text
        assert "color: red" not in text

    def test_empty_html(self):
        """空 HTML 返回空字符串。"""
        parser = HTMLTextExtractor()
        parser.feed("")
        assert parser.get_text() == ""

    def test_nested_elements(self):
        """嵌套元素正确提取。"""
        html = "<div><p>外层<span>内层</span>文本</p></div>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "内层" in text
        assert "文本" in text

    def test_headings_extracted(self):
        """标题标签正确提取。"""
        html = "<h1>一级标题</h1><h2>二级标题</h2><p>正文</p>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "一级标题" in text
        assert "二级标题" in text

    def test_list_items(self):
        """列表项正确提取。"""
        html = "<ul><li>项目1</li><li>项目2</li></ul>"
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        assert "项目1" in text
        assert "项目2" in text


class TestFetchArticles:
    """fetch_articles：数据源唯一，不再回退到已下线的旧源。"""

    @staticmethod
    def _source(articles, *, raises=None):
        source = MagicMock()
        source.get_articles.side_effect = raises
        if raises is None:
            source.get_articles.return_value = articles
        return source

    def test_returns_source_articles(self):
        """直接返回数据源结果，并按 fetch.feed_limit 传窗口。"""
        articles = [{"id": "a1", "title": "文章", "_account_name": "公众号"}]
        source = self._source(articles)

        result = fetch_articles({"fetch": {"feed_limit": 50}}, source)

        assert result == articles
        source.get_articles.assert_called_once_with(limit=50)

    def test_returns_empty_when_source_is_empty(self):
        """数据源返回空列表时返回空，不再触发回退。"""
        assert fetch_articles({}, self._source([])) == []

    def test_returns_empty_on_source_error(self):
        """数据源报错时返回空列表（记录错误），不抛给调用方。"""
        source = self._source(None, raises=requests.RequestException("boom"))

        assert fetch_articles({}, source) == []

    def test_returns_empty_on_invalid_payload(self):
        """数据源返回非法内容（ValueError）时同样降级为空列表。"""
        source = self._source(None, raises=ValueError("bad payload"))

        assert fetch_articles({}, source) == []

    def test_warns_when_window_is_saturated(self, caplog):
        """文章数达到窗口上限时应告警，提示可能被截断。"""
        source = self._source([{"id": str(i)} for i in range(3)])

        with caplog.at_level(logging.WARNING):
            fetch_articles({"fetch": {"feed_limit": 3}}, source)

        assert "上限" in caplog.text
