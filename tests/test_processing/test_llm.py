"""processing/llm.py 测试：prompt 构建、JSON 解析、引号修复、图片上下文。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wx_obsidian.processing.llm as llm_module
from wx_obsidian.processing.llm import (
    _build_images_context,
    _fix_json_quotes,
    _parse_api_response,
    validate_images_field,
)
from wx_obsidian.processing.models import ImageDescription


class TestParseApiResponse:
    """_parse_api_response 测试。"""

    def test_normal_json(self):
        """正常 JSON 直接解析。"""
        text = '{"category": "LLM", "summary": "测试摘要"}'
        result = _parse_api_response(text)
        assert result is not None
        assert result["category"] == "LLM"

    def test_json_in_code_block(self):
        """markdown 代码块包裹的 JSON。"""
        text = '```json\n{"category": "LLM"}\n```'
        result = _parse_api_response(text)
        assert result is not None
        assert result["category"] == "LLM"

    def test_json_with_prose_prefix(self):
        """JSON 前有文字说明。"""
        text = '以下是分析结果：\n{"category": "RAG", "summary": "摘要"}'
        result = _parse_api_response(text)
        assert result is not None
        assert result["category"] == "RAG"

    def test_invalid_json_returns_none(self):
        """完全无法解析时返回 None。"""
        text = "这不是 JSON，也不是代码块"
        result = _parse_api_response(text)
        assert result is None

    def test_empty_string(self):
        """空字符串返回 None。"""
        assert _parse_api_response("") is None

    def test_json_with_unescaped_quotes(self):
        """包含未转义引号的 JSON（通过 _fix_json_quotes 修复）。"""
        text = '{"summary": "他说"你好"世界"}'
        result = _parse_api_response(text)
        # _fix_json_quotes 应该修复引号问题
        assert result is not None

    def test_nested_json_structure(self):
        """嵌套 JSON 结构。"""
        text = json.dumps(
            {
                "category": "LLM",
                "concepts": [{"name": "概念A", "description": "描述A"}],
                "body_sections": [{"heading": "一、标题", "content": "内容"}],
            },
            ensure_ascii=False,
        )
        result = _parse_api_response(text)
        assert result is not None
        assert len(result["concepts"]) == 1
        assert result["concepts"][0]["name"] == "概念A"


class TestFixJsonQuotes:
    """_fix_json_quotes 测试。"""

    def test_normal_json_unchanged(self):
        """正常 JSON 不变。"""
        text = '{"key": "value"}'
        result = _fix_json_quotes(text)
        assert json.loads(result) == {"key": "value"}

    def test_unescaped_inner_quotes(self):
        """修复值内未转义的双引号。"""
        text = '{"summary": "他说"你好"世界"}'
        result = _fix_json_quotes(text)
        # 内部引号应被替换为单引号
        parsed = json.loads(result)
        assert "你好" in parsed["summary"]

    def test_multiple_string_fields(self):
        """多个字符串字段。"""
        text = '{"a": "hello", "b": "world"}'
        result = _fix_json_quotes(text)
        parsed = json.loads(result)
        assert parsed["a"] == "hello"
        assert parsed["b"] == "world"

    def test_trailing_backslash(self):
        """尾部反斜杠边界：不会崩溃。"""
        text = '{"key": "value\\'
        # 应该不会抛异常，即使结果可能不完美
        result = _fix_json_quotes(text)
        assert isinstance(result, str)

    def test_escaped_quotes_preserved(self):
        """已转义的引号保持不变。"""
        text = '{"key": "value with \\"quotes\\""}'
        result = _fix_json_quotes(text)
        parsed = json.loads(result)
        assert "quotes" in parsed["key"]

    def test_empty_object(self):
        """空对象。"""
        text = "{}"
        result = _fix_json_quotes(text)
        assert json.loads(result) == {}

    def test_array_values(self):
        """数组中的字符串值。"""
        text = '{"tags": ["tag1", "tag2"]}'
        result = _fix_json_quotes(text)
        parsed = json.loads(result)
        assert parsed["tags"] == ["tag1", "tag2"]


class TestBuildImagesContext:
    """_build_images_context 测试。"""

    def test_none_returns_empty(self):
        """None 输入返回空字符串。"""
        assert _build_images_context(None) == ""

    def test_empty_list_returns_empty(self):
        """空列表返回空字符串。"""
        assert _build_images_context([]) == ""

    def test_all_non_content_returns_empty(self):
        """所有图片都是非正文内容时返回空字符串。"""
        descs = [
            ImageDescription(url="u", description="d", is_content=False, type="ad", status="ok"),
        ]
        assert _build_images_context(descs) == ""

    def test_api_error_returns_empty(self):
        """api_error 状态的图片被排除。"""
        descs = [
            ImageDescription(
                url="u", description="d", is_content=True, type="diagram", status="api_error"
            ),
        ]
        assert _build_images_context(descs) == ""

    def test_short_description_filtered(self):
        """描述过短（<10 字）的图片被视为噪声，不进 prompt。"""
        descs = [
            ImageDescription(
                url="u", description="流程图", is_content=True, type="diagram", status="ok"
            ),
        ]
        assert _build_images_context(descs) == ""

    def test_content_images_included(self):
        """正文内容图片被包含。"""
        descs = [
            ImageDescription(
                url="https://img1.png",
                description="流程图展示了系统的整体架构设计",
                is_content=True,
                type="diagram",
                status="ok",
            ),
            ImageDescription(
                url="https://img2.png",
                description="扫码关注公众号广告",
                is_content=False,
                type="ad",
                status="ok",
            ),
        ]
        result = _build_images_context(descs)
        assert "流程图展示了系统的整体架构设计" in result
        assert "扫码关注公众号广告" not in result
        assert "[图片1]" in result

    def test_format(self):
        """输出格式正确。"""
        descs = [
            ImageDescription(
                url="https://img.png",
                description="一张说明注意力机制的描述内容",
                is_content=True,
                type="photo",
                status="ok",
            ),
        ]
        result = _build_images_context(descs)
        assert result.startswith("文章中的图片")
        assert "URL: https://img.png" in result
        assert "一张说明注意力机制的描述内容" in result


class TestValidateImagesField:
    """validate_images_field 测试。"""

    def test_empty_list(self):
        """空列表返回空列表。"""
        assert validate_images_field([]) == []

    def test_valid_item(self):
        """有效条目正常解析。"""
        items = [{"url": "https://img.png", "purpose": "说明", "valuable": True}]
        result = validate_images_field(items)
        assert len(result) == 1
        assert result[0]["url"] == "https://img.png"
        assert result[0]["valuable"] is True

    def test_missing_url_skipped(self):
        """缺少 url 的条目被跳过。"""
        items = [{"placement": "## 标题", "purpose": "说明"}]
        result = validate_images_field(items)
        assert len(result) == 0

    def test_non_dict_skipped(self):
        """非 dict 条目被跳过。"""
        items = ["not a dict", 123, None]
        result = validate_images_field(items)
        assert len(result) == 0

    def test_valuable_defaults_true(self):
        """valuable 缺失时默认 True。"""
        items = [{"url": "https://img.png"}]
        result = validate_images_field(items)
        assert result[0]["valuable"] is True

    def test_mixed_valid_invalid(self):
        """混合有效和无效条目。"""
        items = [
            {"url": "https://img.png", "purpose": "说明"},
            {"url": "", "purpose": "说明"},  # 缺 url
            {"url": "https://img2.png"},
        ]
        result = validate_images_field(items)
        assert len(result) == 2


class TestDebugDump:
    """DEBUG 开关必须按取值解析：.env 模板写的是 DEBUG=0。"""

    def test_debug_off_writes_nothing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DEBUG", "0")
        monkeypatch.setattr(llm_module, "SCRIPT_DIR", tmp_path)
        _parse_api_response('{"category": "LLM"}')
        assert not (tmp_path / "last_response.txt").exists()

    def test_debug_on_writes_dump(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DEBUG", "1")
        monkeypatch.setattr(llm_module, "SCRIPT_DIR", tmp_path)
        _parse_api_response('{"category": "LLM"}')
        assert (tmp_path / "last_response.txt").read_text(encoding="utf-8") == (
            '{"category": "LLM"}'
        )

    def test_debug_dump_failure_does_not_break_parsing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """只读目录（冻结包内）下调试落盘失败，不能反过来打断解析。"""
        monkeypatch.setenv("DEBUG", "1")
        monkeypatch.setattr(llm_module, "SCRIPT_DIR", tmp_path)

        def _boom(*_args: object, **_kwargs: object) -> int:
            raise OSError("read-only file system")

        monkeypatch.setattr(Path, "write_text", _boom)
        result = _parse_api_response('{"category": "LLM"}')
        assert result == {"category": "LLM"}
