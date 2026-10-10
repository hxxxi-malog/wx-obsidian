"""processing/images.py 测试：图片提取、关键词、图片插入。"""

from __future__ import annotations

from wx_obsidian.processing.images import (
    RE_BODY_HEADING,
    _extract_keywords,
    _smart_extract_description,
    extract_images_with_context,
    insert_images_into_markdown,
)


class TestExtractImagesWithContext:
    """extract_images_with_context 测试。"""

    def test_normal_extraction(self, sample_html_with_images: str):
        """正常提取微信 CDN 图片。"""
        images = extract_images_with_context(sample_html_with_images)
        # 2 张 mmbiz 图片（排除 non_cdn 和 pic_blank）
        assert len(images) == 2
        assert all("mmbiz" in img["url"] for img in images)
        assert all(img["url"].startswith("http") for img in images)

    def test_data_src_priority(self):
        """data-src 优先于 src。"""
        html = (
            "<p>这是一段足够长的前置说明文字。</p>"
            '<img data-src="https://mmbiz.qpic.cn/real.png" src="https://placeholder.gif">'
        )
        images = extract_images_with_context(html)
        assert len(images) == 1
        assert "real.png" in images[0]["url"]

    def test_fallback_to_src(self):
        """无 data-src 时回退到 src。"""
        html = '<p>这是一段足够长的前置说明文字。</p><img src="https://mmbiz.qpic.cn/fallback.png">'
        images = extract_images_with_context(html)
        assert len(images) == 1
        assert "fallback.png" in images[0]["url"]

    def test_filter_non_cdn(self):
        """过滤非 mmbiz 图片（前置上下文足够长，排除上下文过滤的干扰）。"""
        html = '<p>这是一段足够长的前置说明文字。</p><img src="https://example.com/image.png">'
        images = extract_images_with_context(html)
        assert len(images) == 0

    def test_filter_pic_blank(self):
        """过滤空白占位图。"""
        html = (
            "<p>这是一段足够长的前置说明文字。</p>"
            '<img data-src="https://mmbiz.qpic.cn/mmbiz_png/pic_blank.png">'
        )
        images = extract_images_with_context(html)
        assert len(images) == 0

    def test_filter_non_http(self):
        """过滤非 http URL（如 base64 data URI）。"""
        html = '<p>这是一段足够长的前置说明文字。</p><img src="data:image/gif;base64,R0lGODlh">'
        images = extract_images_with_context(html)
        assert len(images) == 0

    def test_filter_short_context(self):
        """前后文字都少于 10 字时视为装饰图，跳过。"""
        html = '<p>短。</p><img data-src="https://mmbiz.qpic.cn/test.png"><p>也短。</p>'
        images = extract_images_with_context(html)
        assert images == []

    def test_max_images_limit(self, sample_html_with_images: str):
        """max_images 限制提取数量。"""
        images = extract_images_with_context(sample_html_with_images, max_images=1)
        assert len(images) == 1

    def test_empty_html(self):
        """空 HTML 返回空列表。"""
        assert extract_images_with_context("") == []

    def test_no_images(self):
        """无 img 标签返回空列表。"""
        html = "<p>纯文本内容，没有图片</p>"
        assert extract_images_with_context(html) == []

    def test_context_extraction(self):
        """正确提取图片前后的文字上下文。"""
        html = (
            "<p>这是图片前的足够长的前置文字。</p>"
            '<img data-src="https://mmbiz.qpic.cn/test.png">'
            "<p>这是图片后的足够长的后置文字。</p>"
        )
        images = extract_images_with_context(html)
        assert len(images) == 1
        assert "前置文字" in images[0]["before"]
        assert "后置文字" in images[0]["after"]

    def test_html_entity_unescape(self):
        """HTML 实体正确解码。"""
        html = (
            "<p>这是一段足够长的前置说明文字。</p>"
            '<img data-src="https://mmbiz.qpic.cn/test?a=1&amp;b=2">'
        )
        images = extract_images_with_context(html)
        assert len(images) == 1
        assert "&amp;" not in images[0]["url"]
        assert "a=1&b=2" in images[0]["url"]


class TestExtractKeywords:
    """_extract_keywords 测试。"""

    def test_chinese_keywords(self):
        """提取中文关键词（2-6 字符连续中文序列）。"""
        kw = _extract_keywords("深度学习 是 人工智能 的 核心 技术")
        # 正则 [一-鿿]{2,6} 匹配连续中文，空格分隔后各自匹配
        assert "深度学习" in kw
        assert "人工智能" in kw

    def test_english_keywords(self):
        """提取英文关键词（3+ 字符）。"""
        kw = _extract_keywords("Transformer uses attention mechanism")
        assert "transformer" in kw
        assert "attention" in kw
        assert "mechanism" in kw

    def test_mixed_keywords(self):
        """中英文混合提取。"""
        kw = _extract_keywords("GPT 模型使用 Transformer 架构")
        assert "transformer" in kw
        assert "模型" in kw or "架构" in kw

    def test_empty_text(self):
        """空文本返回空集合。"""
        assert _extract_keywords("") == set()

    def test_short_english_ignored(self):
        """英文少于 3 字符被忽略。"""
        kw = _extract_keywords("I am a AI researcher")
        assert "am" not in kw
        assert "ai" in kw or "researcher" in kw

    def test_short_chinese_ignored(self):
        """中文少于 2 字符被忽略。"""
        kw = _extract_keywords("我 是 研究员")
        # 单个中文字符不提取
        assert "我" not in kw


class TestInsertImagesIntoMarkdown:
    """insert_images_into_markdown 测试。"""

    def test_normal_insertion(self):
        """正常插入图片到匹配的正文章节。"""
        md = """## 摘要
摘要内容。

## 一、核心概念

注意力机制 深度学习 模型训练 推理架构 的综合介绍。

## 二、核心拆解

详细分析。
"""
        # 图片上下文需要与章节内容有 >= 4 个关键词重叠
        images = [
            {
                "url": "https://mmbiz.qpic.cn/test.png",
                "before": "注意力机制 深度学习 模型训练 推理架构",
                "after": "上下文说明",
            }
        ]
        result = insert_images_into_markdown(md, images)
        # 应该插入到"一、核心概念"章节，而不是"摘要"章节
        assert "https://mmbiz.qpic.cn/test.png" in result.split("## 一")[1]
        assert "https://mmbiz.qpic.cn/test.png" not in result.split("## 一")[0]

    def test_empty_images(self):
        """无图片时原样返回。"""
        md = "## 一、标题\n内容"
        assert insert_images_into_markdown(md, []) == md

    def test_no_body_sections(self):
        """无正文章节时不插入。"""
        md = "## 摘要\n摘要内容\n## 核心观点\n- 观点1"
        images = [{"url": "https://mmbiz.qpic.cn/test.png", "before": "测试", "after": ""}]
        result = insert_images_into_markdown(md, images)
        # 无 ## 一、二、等正文章节，不插入
        assert "![" not in result

    def test_max_two_per_section(self):
        """每个 section 最多插入 2 张图片。"""
        md = """## 一、核心概念

注意力机制 深度学习 模型训练 推理架构 的综合介绍。

## 二、核心拆解

其他内容。
"""
        images = [
            {
                "url": f"https://mmbiz.qpic.cn/img{i}.png",
                "before": "注意力机制 深度学习 模型训练 推理架构",
                "after": "上下文说明",
            }
            for i in range(5)
        ]
        result = insert_images_into_markdown(md, images)
        # 单个 section 恰好被限制为 2 张
        section_1 = result.split("## 二")[0]
        assert section_1.count("![") == 2


class TestSmartExtractDescription:
    """_smart_extract_description 测试（alt text 提取）。"""

    def test_extracts_last_complete_sentence(self):
        """优先返回最靠近图片的完整句子（去掉开头不完整片段）。"""
        desc = _smart_extract_description("不完整的前半部分。完整的后半部分句子内容如下。")
        assert "完整的后半部分句子内容如下" in desc
        assert "不完整的前半部分" not in desc

    def test_prefers_before_text_over_after(self):
        """前后文都有内容时，取图片前最近的完整句子。"""
        desc = _smart_extract_description("注意力机制的核心设计哲学。", "补充说明")
        assert "注意力机制的核心设计哲学" in desc

    def test_uses_after_text_when_before_is_short(self):
        """图片前文不足时退回图片后文。"""
        desc = _smart_extract_description("图", "完整的图片说明文字内容。")
        assert "完整的图片说明文字内容" in desc

    def test_empty_text(self):
        """空文本返回空字符串。"""
        assert _smart_extract_description("") == ""

    def test_short_text_falls_back_to_raw_text(self):
        """没有长度 > 10 的完整句子时，回退到清理后的原文。"""
        assert _smart_extract_description("短句。", "") == "短句。"

    def test_caps_length(self):
        """返回的描述长度不超过 125 字符。"""
        long_text = "这是一段很长的关于注意力机制与深度学习模型的描述文字" * 10 + "。"
        desc = _smart_extract_description(long_text, "")
        assert 0 < len(desc) <= 125


class TestRegexPatterns:
    """正则模式测试。"""

    def test_body_heading_chinese(self):
        """匹配中文数字章节标题。"""
        assert RE_BODY_HEADING.match("## 一、核心概念")
        assert RE_BODY_HEADING.match("## 二、核心拆解")
        assert RE_BODY_HEADING.match("## 三、核心洞察")

    def test_body_heading_numbered(self):
        """匹配数字编号章节标题。"""
        assert RE_BODY_HEADING.match("## 1. 概述")
        assert RE_BODY_HEADING.match("## 2. 分析")

    def test_body_heading_no_match_summary(self):
        """不匹配非正文章节。"""
        assert not RE_BODY_HEADING.match("## 摘要")
        assert not RE_BODY_HEADING.match("## 核心观点")
        assert not RE_BODY_HEADING.match("## 关键概念")
