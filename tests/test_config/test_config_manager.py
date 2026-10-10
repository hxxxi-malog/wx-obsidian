"""config_manager.py 单元测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from wx_obsidian.config_manager import _BUILTIN_CHANNELS, _DEFAULT_CONFIG, ConfigManager
from wx_obsidian.models import ConnectionTestResult

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

# 会通过 .env 注入 os.environ 的键，测试结束后需要还原
_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "VISION_API_KEY",
    "DEEPSEEK_BASE_URL",
    "MODEL_NAME",
    "VISION_BASE_URL",
    "VISION_MODEL_NAME",
)


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """隔离旧配置路径、默认配置和环境变量，防止测试间互相污染。"""
    import wx_obsidian.config_manager as cm_module

    fake_old = tmp_path / "nonexistent_old"
    monkeypatch.setattr(cm_module, "_OLD_CONFIG_YAML", fake_old / "config.yaml")
    monkeypatch.setattr(cm_module, "_OLD_ENV_FILE", fake_old / ".env")
    # 每个测试用深拷贝的默认配置，防止跨测试污染
    monkeypatch.setattr(cm_module, "_DEFAULT_CONFIG", copy.deepcopy(_DEFAULT_CONFIG))
    # _load_env() 会直接写 os.environ，这里清空并由 monkeypatch 在收尾时还原，
    # 保证不依赖开发机真实环境变量。
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """返回临时配置目录。"""
    d = tmp_path / ".wx-obsidian"
    d.mkdir()
    return d


@pytest.fixture
def cm(config_dir: Path) -> ConfigManager:
    """返回使用临时目录的 ConfigManager。"""
    return ConfigManager(config_dir=config_dir)


@pytest.fixture
def env_file(config_dir: Path) -> Path:
    """创建包含测试 API key 的 .env 文件。"""
    env = config_dir / ".env"
    env.write_text("DEEPSEEK_API_KEY=sk-test123\nVISION_API_KEY=sk-vision\n", encoding="utf-8")
    return env


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


class TestLoad:
    """ConfigManager.load() 测试。"""

    def test_load_existing_json(self, cm: ConfigManager, config_dir: Path) -> None:
        """正常加载 config.json，用户值覆盖默认值。"""
        data = {
            "llm": {"active": "custom", "providers": {"custom": {"model": "deepseek-v3"}}},
            "obsidian": {"articles_dir": "我的文章"},
        }
        (config_dir / "config.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

        result = cm.load()

        assert result["llm"]["active"] == "custom"
        assert result["llm"]["providers"]["custom"]["model"] == "deepseek-v3"
        assert result["obsidian"]["articles_dir"] == "我的文章"
        # 未在 config.json 中出现的字段由默认配置补齐
        assert result["fetch"]["max_workers"] == _DEFAULT_CONFIG["fetch"]["max_workers"]

    def test_load_fallback_to_defaults(self, cm: ConfigManager) -> None:
        """config.json 不存在时返回默认配置。"""
        result = cm.load()
        assert result["version"] == _DEFAULT_CONFIG["version"]
        assert result["llm"]["active"] == "deepseek"
        assert result["source"]["type"] == "we_mp_rss"

    def test_load_corrupted_json_falls_back(self, cm: ConfigManager, config_dir: Path) -> None:
        """config.json 损坏时回退到默认配置。"""
        (config_dir / "config.json").write_text("{broken json", encoding="utf-8")
        result = cm.load()
        # 回退到默认（无旧 yaml 时）
        assert result["version"] == _DEFAULT_CONFIG["version"]
        assert result["llm"]["active"] == "deepseek"

    def test_load_keeps_scalar_overrides(self, cm: ConfigManager, config_dir: Path) -> None:
        """顶层标量字段由 config.json 覆盖默认值（新配置优先）。"""
        (config_dir / "config.json").write_text(json.dumps({"version": "2.0"}), encoding="utf-8")
        assert cm.load()["version"] == "2.0"

    def test_load_keeps_default_when_section_shape_is_wrong(
        self, cm: ConfigManager, config_dir: Path
    ) -> None:
        """段落被写成标量时保留默认段落，避免下游 .get() 崩溃。"""
        (config_dir / "config.json").write_text(json.dumps({"llm": "oops"}), encoding="utf-8")
        assert cm.load()["llm"]["active"] == _DEFAULT_CONFIG["llm"]["active"]

    def test_load_auto_loads_on_get(self, cm: ConfigManager, config_dir: Path) -> None:
        """未显式 load() 时 get() 自动加载。"""
        data = {"llm": {"active": "custom", "providers": {"custom": {"model": "auto-loaded"}}}}
        (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")
        # 不调用 cm.load()
        assert cm.get("llm.providers.custom.model") == "auto-loaded"


class TestSave:
    """ConfigManager.save() 测试。"""

    def test_save_creates_file(self, cm: ConfigManager, config_dir: Path) -> None:
        """save() 创建 config.json。"""
        cm.load()
        cm.set("llm.providers.deepseek.model", "saved-model")
        cm.save()

        saved = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert saved["llm"]["providers"]["deepseek"]["model"] == "saved-model"

    def test_save_leaves_no_temp_file(self, cm: ConfigManager, config_dir: Path) -> None:
        """save() 后不存在临时文件。"""
        cm.load()
        cm.save()

        assert list(config_dir.glob(".config_*.tmp")) == []

    def test_save_preserves_on_failure(self, cm: ConfigManager, config_dir: Path) -> None:
        """写入失败时原文件不受影响，临时文件被清理。"""
        cm.load()
        cm.set("version", "1.0")
        cm.save()

        # 修改后再保存，模拟失败
        cm.set("version", "2.0")
        with (
            patch("os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            cm.save()

        saved = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert saved["version"] == "1.0"
        assert list(config_dir.glob(".config_*.tmp")) == []

    def test_save_accepts_explicit_config(self, cm: ConfigManager, config_dir: Path) -> None:
        """save(config) 可以传入完整配置。"""
        custom = {"custom_key": "value"}
        cm.save(config=custom)

        saved = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert saved["custom_key"] == "value"
        assert cm.get("custom_key") == "value"

    def test_save_creates_missing_directory(self, tmp_path: Path) -> None:
        """目标目录不存在时自动创建。"""
        target = tmp_path / "brand-new" / "cfg"
        manager = ConfigManager(config_dir=target)
        manager.load()
        manager.save()

        assert (target / "config.json").exists()


# ---------------------------------------------------------------------------
# get / set
# ---------------------------------------------------------------------------


class TestGetSet:
    """ConfigManager.get()/set() 测试。"""

    def test_get_flat_key(self, cm: ConfigManager) -> None:
        cm.load()
        assert cm.get("version") == _DEFAULT_CONFIG["version"]

    def test_get_nested_key(self, cm: ConfigManager) -> None:
        cm.load()
        assert cm.get("llm.active") == "deepseek"
        assert (
            cm.get("llm.providers.deepseek.model") == _BUILTIN_CHANNELS["llm"]["deepseek"]["model"]
        )

    def test_get_missing_returns_default(self, cm: ConfigManager) -> None:
        cm.load()
        assert cm.get("nonexistent", "fallback") == "fallback"

    def test_get_missing_nested_returns_default(self, cm: ConfigManager) -> None:
        cm.load()
        assert cm.get("llm.nonexistent.deep", None) is None

    def test_get_partial_path_returns_default(self, cm: ConfigManager) -> None:
        """路径中间层不是 dict 时返回 default。"""
        cm.load()
        assert cm.get("version.sub", "default") == "default"

    def test_set_flat_key(self, cm: ConfigManager) -> None:
        cm.load()
        cm.set("version", "2.0")
        assert cm.get("version") == "2.0"

    def test_set_nested_key(self, cm: ConfigManager) -> None:
        cm.load()
        cm.set("llm.providers.deepseek.model", "new-model")
        assert cm.get("llm.providers.deepseek.model") == "new-model"

    def test_set_creates_intermediate_dict(self, cm: ConfigManager) -> None:
        """set() 自动创建中间层 dict。"""
        cm.load()
        cm.set("new_section.sub.key", "value")
        assert cm.get("new_section.sub.key") == "value"

    def test_set_overwrites_non_dict_with_dict(self, cm: ConfigManager) -> None:
        """中间层不是 dict 时被替换为 dict。"""
        cm.load()
        cm.set("version.nested", "value")
        assert cm.get("version.nested") == "value"


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    """ConfigManager.validate() 测试。"""

    def test_validate_empty_vault_path(self, cm: ConfigManager) -> None:
        """vault_path 未设置时报告错误。"""
        cm.load()
        errors = cm.validate()
        assert any("vault_path" in e for e in errors)

    def test_validate_missing_source_url(self, cm: ConfigManager, config_dir: Path) -> None:
        """source.base_url 未设置时报告错误。"""
        cfg = {**_DEFAULT_CONFIG, "source": {**_DEFAULT_CONFIG["source"], "base_url": ""}}
        (config_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        cm.load()
        errors = cm.validate()
        assert any("source.base_url" in e for e in errors)

    def test_validate_invalid_source_type(self, cm: ConfigManager, config_dir: Path) -> None:
        """source.type 取非法值时报告错误。"""
        cfg = {**_DEFAULT_CONFIG, "source": {**_DEFAULT_CONFIG["source"], "type": "bogus"}}
        (config_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        cm.load()
        errors = cm.validate()
        assert any("source.type" in e for e in errors)

    def test_validate_missing_api_key(self, cm: ConfigManager) -> None:
        """DEEPSEEK_API_KEY 未设置时报告错误。"""
        cm.load()
        errors = cm.validate()
        assert any("DEEPSEEK_API_KEY" in e for e in errors)

    def test_validate_passes_with_valid_config(
        self, cm: ConfigManager, config_dir: Path, env_file: Path
    ) -> None:
        """所有必须字段填写时通过校验。"""
        cfg = {**_DEFAULT_CONFIG, "obsidian": {"vault_path": str(config_dir / "vault")}}
        (config_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        cm.load()
        errors = cm.validate()
        assert errors == []

    def test_validate_vault_path_parent_not_writable(
        self, cm: ConfigManager, config_dir: Path, env_file: Path
    ) -> None:
        """vault_path 父目录不可写时报告错误。"""
        cfg = {**_DEFAULT_CONFIG, "obsidian": {"vault_path": "/nonexistent/path/vault"}}
        (config_dir / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        cm.load()
        errors = cm.validate()
        assert any("不存在" in e or "不可写" in e for e in errors)


# ---------------------------------------------------------------------------
# is_first_run
# ---------------------------------------------------------------------------


class TestIsFirstRun:
    """ConfigManager.is_first_run() 测试。"""

    def test_first_run_when_both_missing(self, cm: ConfigManager) -> None:
        assert cm.is_first_run() is True

    def test_not_first_run_when_config_exists(self, cm: ConfigManager, config_dir: Path) -> None:
        (config_dir / "config.json").write_text("{}", encoding="utf-8")
        assert cm.is_first_run() is False

    def test_not_first_run_when_env_exists(self, cm: ConfigManager, env_file: Path) -> None:
        assert cm.is_first_run() is False


# ---------------------------------------------------------------------------
# migrate_from_old
# ---------------------------------------------------------------------------


class TestMigrateFromOld:
    """ConfigManager.migrate_from_old() 测试。"""

    def test_migrate_no_old_config(self, cm: ConfigManager, config_dir: Path) -> None:
        """无旧配置时生成默认 config.json。"""
        report = cm.migrate_from_old()
        assert report.success is True
        assert any("config.json" in item for item in report.migrated_items)

    def test_migrate_with_existing_config_json(self, cm: ConfigManager, config_dir: Path) -> None:
        """已有 config.json 时保留其值。"""
        existing = {"version": "2.0", "custom": "preserved"}
        (config_dir / "config.json").write_text(json.dumps(existing), encoding="utf-8")
        report = cm.migrate_from_old()
        assert report.success is True
        loaded = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert loaded["version"] == "2.0"
        assert loaded["custom"] == "preserved"

    def test_migrate_idempotent(self, cm: ConfigManager, config_dir: Path) -> None:
        """多次迁移结果一致（幂等性）。"""
        cm.migrate_from_old()
        first = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        cm.migrate_from_old()
        second = json.loads((config_dir / "config.json").read_text(encoding="utf-8"))
        assert first == second

    def test_migrate_creates_directory(self, tmp_path: Path) -> None:
        """目标目录不存在时自动创建。"""
        new_dir = tmp_path / "new_config"
        cm = ConfigManager(config_dir=new_dir)
        report = cm.migrate_from_old()
        assert report.success is True
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# 连通性测试（mock requests）
# ---------------------------------------------------------------------------


class TestConnectionTests:
    """ConfigManager 连通性测试（全部 mock 出站 HTTP）。"""

    @staticmethod
    def _ok_response() -> MagicMock:
        resp = MagicMock()
        resp.status_code = 200
        return resp

    def test_llm_connection_no_key(self, cm: ConfigManager) -> None:
        """API key 未设置时返回失败。"""
        cm.load()
        result = cm.test_llm_connection()
        assert result.success is False
        assert "未设置" in result.message

    def test_llm_connection_success(self, cm: ConfigManager, env_file: Path) -> None:
        """LLM API 可达时返回成功，请求带上 Bearer 认证。"""
        cm.load()
        with patch(
            "wx_obsidian.config_manager.requests.get", return_value=self._ok_response()
        ) as mock_get:
            result = cm.test_llm_connection()

        assert result.success is True
        assert result.details is not None
        assert set(result.details) == {"model"}
        assert mock_get.call_args.args[0] == "https://api.deepseek.com/models"
        assert mock_get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test123"

    def test_llm_connection_failure(self, cm: ConfigManager, env_file: Path) -> None:
        """LLM API 返回非 200 时返回失败。"""
        cm.load()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        with patch("wx_obsidian.config_manager.requests.get", return_value=mock_resp):
            result = cm.test_llm_connection()
        assert result.success is False
        assert "401" in result.message

    def test_llm_connection_timeout(self, cm: ConfigManager, env_file: Path) -> None:
        """LLM API 超时时返回失败。"""
        cm.load()
        with patch(
            "wx_obsidian.config_manager.requests.get",
            side_effect=requests.Timeout("timeout"),
        ):
            result = cm.test_llm_connection()
        assert result.success is False
        assert "连接失败" in result.message

    def test_vision_connection_no_key(self, cm: ConfigManager) -> None:
        """VISION_API_KEY 未设置时返回失败。"""
        cm.load()
        result = cm.test_vision_connection()
        assert result.success is False
        assert "未设置" in result.message

    def test_vision_connection_success(self, cm: ConfigManager, env_file: Path) -> None:
        """VISION_API_KEY 存在且 API 可达时返回成功。"""
        cm.load()
        with patch("wx_obsidian.config_manager.requests.get", return_value=self._ok_response()):
            result = cm.test_vision_connection()
        assert result.success is True
        assert result.details is not None

    def test_source_connection_we_mp_rss(self, cm: ConfigManager) -> None:
        """默认数据源 we-mp-rss 探活打到 /feed/all.json。"""
        cm.load()
        with patch(
            "wx_obsidian.config_manager.requests.get", return_value=self._ok_response()
        ) as mock_get:
            result = cm.test_source_connection()

        assert result.success is True
        assert "we-mp-rss" in result.message
        assert mock_get.call_args.args[0] == "http://localhost:8001/feed/all.json?limit=1"

    def test_source_connection_failure(self, cm: ConfigManager) -> None:
        """数据源不可达时返回失败。"""
        cm.load()
        with patch(
            "wx_obsidian.config_manager.requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            result = cm.test_source_connection()
        assert result.success is False
        assert "连接失败" in result.message

    def test_source_connection_http_error(self, cm: ConfigManager) -> None:
        """数据源返回非 200 时报告 HTTP 状态码。"""
        cm.load()
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("wx_obsidian.config_manager.requests.get", return_value=mock_resp):
            result = cm.test_source_connection()
        assert result.success is False
        assert "HTTP 500" in result.message

    def test_all_connections(self, cm: ConfigManager, env_file: Path) -> None:
        """test_all_connections 返回 llm / vision / source 三个键。"""
        cm.load()
        with patch("wx_obsidian.config_manager.requests.get", return_value=self._ok_response()):
            results = cm.test_all_connections()

        assert set(results.keys()) == {"llm", "vision", "source"}
        assert all(isinstance(r, ConnectionTestResult) for r in results.values())
        assert all(r.success for r in results.values())
