"""processing/vision.py 测试：预过滤、响应解析、描述函数。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from wx_obsidian.processing.vision import (
    _parse_vision_response,
    _pre_filter_images,
    describe_images,
)


class TestPreFilterImages:
    """_pre_filter_images 测试。"""

    def test_empty_list(self):
        """空列表返回空列表。"""
        assert _pre_filter_images([]) == []

    def test_keeps_normal_mmbiz_url(self):
        """保留正常 mmbiz CDN 图片。"""
        images = [{"url": "https://mmbiz.qpic.cn/mmbiz_png/test.png", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 1

    def test_filters_qr_code_url(self):
        """过滤包含 qr_code 的 URL。"""
        images = [{"url": "https://mmbiz.qpic.cn/mmbiz_qrcode/qr.png", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 0

    def test_filters_ad_url(self):
        """过滤包含 ad_ 的 URL。"""
        images = [{"url": "https://mmbiz.qpic.cn/ad_banner.png", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 0

    def test_filters_sponsor_url(self):
        """过滤包含 sponsor 的 URL。"""
        images = [{"url": "https://mmbiz.qpic.cn/sponsor_img.jpg", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 0

    def test_keeps_url_matching_both_patterns(self):
        """URL 同时匹配内容和广告模式时保留（宁可多调用 API）。"""
        images = [{"url": "https://mmbiz.qpic.cn/content_flowchart.png", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 1

    def test_skips_empty_url(self):
        """跳过空 URL。"""
        images = [{"url": "", "before": "", "after": ""}]
        result = _pre_filter_images(images)
        assert len(result) == 0

    def test_mixed_filter(self):
        """混合过滤：部分保留部分过滤。"""
        images = [
            {"url": "https://mmbiz.qpic.cn/normal.png", "before": "", "after": ""},
            {"url": "https://mmbiz.qpic.cn/qr_code.png", "before": "", "after": ""},
            {"url": "https://mmbiz.qpic.cn/follow.jpg", "before": "", "after": ""},
        ]
        result = _pre_filter_images(images)
        assert len(result) == 1
        assert "normal" in result[0]["url"]


class TestParseVisionResponse:
    """_parse_vision_response 测试。"""

    def test_valid_json(self):
        """有效 JSON 正常解析。"""
        text = json.dumps(
            {
                "description": "流程图",
                "is_content": True,
                "type": "diagram",
            }
        )
        result = _parse_vision_response(text, "https://test.png")
        assert result.description == "流程图"
        assert result.is_content is True
        assert result.type == "diagram"
        assert result.status == "ok"

    def test_missing_fields_use_defaults(self):
        """缺失字段使用默认值。"""
        text = '{"description": "图片"}'
        result = _parse_vision_response(text, "https://test.png")
        assert result.description == "图片"
        assert result.is_content is False
        assert result.type == "unknown"
        assert result.status == "ok"

    def test_invalid_json_returns_error_status(self):
        """无效 JSON 返回 api_error 状态。"""
        result = _parse_vision_response("not json", "https://test.png")
        assert result.status == "api_error"
        assert result.is_content is False

    def test_json_in_code_block(self):
        """markdown 代码块包裹的 JSON。"""
        text = '```json\n{"description": "test", "is_content": true, "type": "photo"}\n```'
        result = _parse_vision_response(text, "https://test.png")
        assert result.description == "test"
        assert result.status == "ok"

    def test_url_preserved(self):
        """URL 被正确保留。"""
        text = '{"description": "test"}'
        result = _parse_vision_response(text, "https://mmbiz.qpic.cn/test.png")
        assert result.url == "https://mmbiz.qpic.cn/test.png"


class TestDescribeImages:
    """describe_images 测试。"""

    def test_empty_list(self):
        """空列表返回空结果。"""
        result = describe_images(
            [], {"api_key": "test", "base_url": "http://test", "model": "test"}
        )
        assert result == []

    def test_all_filtered(self):
        """所有图片被预过滤时返回空结果。"""
        images = [{"url": "https://mmbiz.qpic.cn/qr_code.png", "before": "", "after": ""}]
        result = describe_images(
            images, {"api_key": "test", "base_url": "http://test", "model": "test"}
        )
        assert result == []

    @patch("wx_obsidian.processing.vision.requests.post")
    def test_single_image_success(self, mock_post: MagicMock):
        """单张图片成功描述。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "description": "流程图展示了工作原理",
                                "is_content": True,
                                "type": "diagram",
                            }
                        )
                    }
                }
            ],
        }
        mock_post.return_value = mock_response

        images = [
            {"url": "https://mmbiz.qpic.cn/test.png", "before": "上下文前", "after": "上下文后"}
        ]
        config = {"api_key": "test", "base_url": "http://test", "model": "test", "timeout": 10}
        result = describe_images(images, config)

        assert len(result) == 1
        assert result[0].description == "流程图展示了工作原理"
        assert result[0].is_content is True
        assert result[0].status == "ok"

    @patch("wx_obsidian.processing.vision.requests.post")
    def test_single_image_api_error(self, mock_post: MagicMock):
        """单张图片 API 失败返回 api_error 状态。"""
        import requests as req

        mock_post.side_effect = req.RequestException("connection error")

        images = [{"url": "https://mmbiz.qpic.cn/test.png", "before": "", "after": ""}]
        config = {
            "api_key": "test",
            "base_url": "http://test",
            "model": "test",
            "timeout": 1,
            "max_retries": 0,
        }
        result = describe_images(images, config)

        assert len(result) == 1
        assert result[0].status == "api_error"
        assert result[0].is_content is False
