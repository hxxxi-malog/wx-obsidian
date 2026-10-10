"""模型渠道（LLM / Vision）与图片内联的回归测试。

覆盖两块按用户需求新增的能力：
1. llm/vision 支持多渠道路由（active + providers），且能从旧的扁平配置迁移；
2. Vision 把图片本地下载后内联成 data URL —— 因为微信 CDN 有防盗链，
   多模态服务端抓不到（实测 DeepSeek 报 "Failed to download image"）。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.processing.vision import _describe_single, load_image_as_data_url


@pytest.fixture
def cm(tmp_path: Path) -> ConfigManager:
    """使用临时配置目录，避免读写用户真实配置。"""
    manager = ConfigManager(config_dir=tmp_path / "cfg")
    manager.load()
    return manager


# ---------------------------------------------------------------------------
# 渠道解析与切换
# ---------------------------------------------------------------------------


class TestModelChannels:
    """llm / vision 的渠道路由。"""

    def test_defaults_route_vision_to_deepseek(self, cm: ConfigManager) -> None:
        """默认视觉渠道是 DeepSeek 的 deepseek-flash（该模型支持图像输入）。"""
        vision = cm.resolve_vision_channel()
        assert vision["provider"] == "deepseek"
        assert vision["model"] == "deepseek-flash"
        assert "api.deepseek.com" in vision["base_url"]

    def test_defaults_route_llm_to_deepseek(self, cm: ConfigManager) -> None:
        """语言模型默认也走 deepseek-flash，与视觉渠道统一。

        用户明确要求两个渠道都不再使用 deepseek-v4-pro。
        """
        llm = cm.resolve_llm_channel()
        assert llm["provider"] == "deepseek"
        assert llm["model"] == "deepseek-flash"

    def test_no_channel_defaults_to_v4_pro(self, cm: ConfigManager) -> None:
        """任何内置渠道的默认模型都不应再是 deepseek-v4-pro。"""
        for section in ("llm", "vision"):
            for name in cm.channel_names(section):
                model = cm.get(f"{section}.providers.{name}.model")
                assert model != "deepseek-v4-pro", f"{section}.{name} 仍在使用 deepseek-v4-pro"

    def test_builtin_channels_are_switchable(self, cm: ConfigManager) -> None:
        """内置渠道开箱可切换，切换后 model/base_url 随之改变。"""
        assert set(cm.channel_names("vision")) >= {"deepseek", "dashscope"}

        assert cm.set_active_channel("vision", "dashscope") is True
        switched = cm.resolve_vision_channel()
        assert switched["provider"] == "dashscope"
        assert switched["model"] == "qwen-vl-plus"

        assert cm.set_active_channel("vision", "deepseek") is True
        assert cm.resolve_vision_channel()["model"] == "deepseek-flash"

    def test_switch_to_unknown_channel_is_rejected(self, cm: ConfigManager) -> None:
        """切到不存在的渠道返回 False，且不改变当前生效渠道。"""
        before = cm.resolve_llm_channel()["provider"]
        assert cm.set_active_channel("llm", "not-exist") is False
        assert cm.resolve_llm_channel()["provider"] == before

    def test_user_can_add_custom_channel(self, cm: ConfigManager) -> None:
        """允许用户新增任意 OpenAI 兼容渠道并切过去。"""
        cm.set(
            "llm.providers.mychannel",
            {
                "base_url": "https://example.com/v1",
                "model": "my-model",
                "api_key_env": "MY_KEY",
            },
        )
        assert cm.set_active_channel("llm", "mychannel") is True
        resolved = cm.resolve_llm_channel()
        assert resolved["base_url"] == "https://example.com/v1"
        assert resolved["model"] == "my-model"

    def test_channel_api_key_resolution(self, cm: ConfigManager, monkeypatch: Any) -> None:
        """内联 api_key 优先；否则按 api_key_env 取环境变量。"""
        monkeypatch.setenv("MY_KEY", "from-env")
        cm.set(
            "llm.providers.mychannel",
            {"base_url": "https://x/v1", "model": "m", "api_key_env": "MY_KEY"},
        )
        cm.set_active_channel("llm", "mychannel")
        assert cm.resolve_llm_channel()["api_key"] == "from-env"

        cm.set("llm.providers.mychannel.api_key", "inline-key")
        assert cm.resolve_llm_channel()["api_key"] == "inline-key"

    def test_migrates_legacy_flat_config(self, tmp_path: Path) -> None:
        """旧的扁平 provider/base_url/model 会被整理成渠道，且值不丢。"""
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "llm": {
                        "provider": "deepseek",
                        "base_url": "https://api.deepseek.com",
                        "model": "deepseek-v4-pro",
                        "max_tokens": 4096,
                        "temperature": 0.7,
                    },
                    "vision": {
                        "enabled": True,
                        "provider": "dashscope",
                        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                        "model": "qwen-vl-plus",
                        "max_concurrency": 10,
                        "timeout": 120,
                    },
                    "obsidian": {"vault_path": "/tmp/v", "articles_dir": "a"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        manager = ConfigManager(config_dir=cfg_dir)
        manager.load()

        # 扁平字段被收进以其 provider 命名的渠道
        assert manager.get("llm.providers.deepseek.model") == "deepseek-v4-pro"
        assert manager.get("vision.providers.dashscope.model") == "qwen-vl-plus"
        # 扁平字段已清理，避免与新结构并存产生歧义
        assert manager.get("llm.base_url") is None
        assert manager.get("vision.provider") is None
        # 旧的 vision.enabled 被保留
        assert manager.get("vision.enabled") is True

    def test_vision_max_tokens_has_reasoning_headroom(self, cm: ConfigManager) -> None:
        """视觉输出预算必须足够大：推理型模型在 1024 下会被截断。"""
        assert cm.resolve_vision_channel()["max_tokens"] >= 2048
        assert cm.resolve_vision_channel()["inline_images"] is True


# ---------------------------------------------------------------------------
# 图片内联
# ---------------------------------------------------------------------------


class TestInlineImage:
    """load_image_as_data_url：微信图防盗链导致服务端抓不到，需本地下载内联。"""

    @staticmethod
    def _resp(
        content: bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 100, content_type: str = "image/png"
    ) -> MagicMock:
        resp = MagicMock()
        resp.content = content
        resp.headers = {"Content-Type": content_type}
        resp.raise_for_status = MagicMock()
        return resp

    def test_returns_data_url(self) -> None:
        with patch(
            "wx_obsidian.processing.vision.requests.get", return_value=self._resp()
        ) as mock_get:
            result = load_image_as_data_url("https://mmbiz.qpic.cn/a.png", timeout=5)

        assert result.startswith("data:image/png;base64,")
        decoded = base64.b64decode(result.split(",", 1)[1])
        assert decoded.startswith(b"\x89PNG")
        # mmbiz 有防盗链，必须带浏览器 UA
        assert "User-Agent" in mock_get.call_args.kwargs["headers"]
        assert mock_get.call_args.kwargs["timeout"] == 5

    def test_rejects_non_image_content_type(self) -> None:
        with patch(
            "wx_obsidian.processing.vision.requests.get",
            return_value=self._resp(b"<html>", "text/html"),
        ):
            assert load_image_as_data_url("https://x/a") == ""

    def test_rejects_oversized_image(self) -> None:
        big = b"x" * (9 * 1024 * 1024)
        with patch("wx_obsidian.processing.vision.requests.get", return_value=self._resp(big)):
            assert load_image_as_data_url("https://x/a") == ""

    def test_returns_empty_on_network_error(self) -> None:
        with patch(
            "wx_obsidian.processing.vision.requests.get",
            side_effect=requests.RequestException("boom"),
        ):
            assert load_image_as_data_url("https://x/a") == ""

    def test_returns_empty_for_non_http_url(self) -> None:
        assert load_image_as_data_url("data:image/png;base64,AAAA") == ""


class TestDescribeSingleInlining:
    """_describe_single 发送的图片引用形态。"""

    def _cfg(self, **overrides: Any) -> dict[str, Any]:
        cfg: dict[str, Any] = {
            "api_key": "k",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "timeout": 10,
            "max_retries": 0,
            "max_tokens": 3072,
            "inline_images": True,
        }
        cfg.update(overrides)
        return cfg

    @staticmethod
    def _api_resp() -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [
                {"message": {"content": '{"description":"d","is_content":true,"type":"photo"}'}}
            ]
        }
        return resp

    def test_sends_data_url_when_inlining(self) -> None:
        img = {"url": "https://mmbiz.qpic.cn/a.png", "before": "上文", "after": "下文"}
        with (
            patch(
                "wx_obsidian.processing.vision.load_image_as_data_url",
                return_value="data:image/png;base64,AAA",
            ) as mock_load,
            patch(
                "wx_obsidian.processing.vision.requests.post", return_value=self._api_resp()
            ) as mock_post,
        ):
            result = _describe_single(img, self._cfg())

        mock_load.assert_called_once()
        sent = mock_post.call_args.kwargs["json"]
        assert sent["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,AAA"
        assert sent["max_tokens"] == 3072
        assert result.status == "ok"

    def test_falls_back_to_raw_url_when_download_fails(self) -> None:
        """下载失败时退回原 URL，保留「让服务端自己抓」的旧行为。"""
        img = {"url": "https://mmbiz.qpic.cn/a.png", "before": "上文", "after": "下文"}
        with (
            patch("wx_obsidian.processing.vision.load_image_as_data_url", return_value=""),
            patch(
                "wx_obsidian.processing.vision.requests.post", return_value=self._api_resp()
            ) as mock_post,
        ):
            _describe_single(img, self._cfg())

        sent = mock_post.call_args.kwargs["json"]
        assert (
            sent["messages"][0]["content"][1]["image_url"]["url"] == "https://mmbiz.qpic.cn/a.png"
        )

    def test_can_disable_inlining(self) -> None:
        img = {"url": "https://x/a.png", "before": "上文", "after": "下文"}
        with (
            patch("wx_obsidian.processing.vision.load_image_as_data_url") as mock_load,
            patch(
                "wx_obsidian.processing.vision.requests.post", return_value=self._api_resp()
            ) as mock_post,
        ):
            _describe_single(img, self._cfg(inline_images=False))

        mock_load.assert_not_called()
        sent = mock_post.call_args.kwargs["json"]
        assert sent["messages"][0]["content"][1]["image_url"]["url"] == "https://x/a.png"
