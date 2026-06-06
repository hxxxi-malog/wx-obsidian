"""配置管理：读写 ~/.wx-obsidian/config.json，迁移旧配置，连通性测试。"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from wx_obsidian.models import ConnectionTestResult, MigrationReport

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".wx-obsidian"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / ".env"
LOGS_DIR = CONFIG_DIR / "logs"
FAILED_FILE = CONFIG_DIR / "failed.json"

# 旧配置路径（项目目录）
_OLD_CONFIG_YAML = Path(__file__).parent.parent / "config.yaml"
_OLD_ENV_FILE = Path(__file__).parent.parent / ".env"

_DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "llm": {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "vision": {
        "enabled": False,
        "provider": "dashscope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-vl-plus",
        "max_concurrency": 10,
        "timeout": 120,
    },
    "obsidian": {
        "vault_path": "",
        "articles_dir": "公众号文章",
    },
    "wewe_rss": {
        "base_url": "http://localhost:4000",
    },
    "fetch": {
        "max_days": 7,
        "max_workers": 5,
    },
    "scheduler": {
        "fetch_cron": "0 */2 * * *",
        "keepalive_interval_days": 7,
    },
}


# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------


class ConfigManager:
    """配置管理器：读写 ~/.wx-obsidian/config.json。"""

    def __init__(self, config_dir: Path = CONFIG_DIR) -> None:
        self._config_dir = config_dir
        self._config_file = config_dir / "config.json"
        self._env_file = config_dir / ".env"
        self._config: dict[str, Any] = {}
        self._loaded = False

    # -- 读写 ---------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """加载配置。优先读 config.json，回退到旧 config.yaml。"""
        if self._config_file.exists():
            try:
                self._config = json.loads(self._config_file.read_text(encoding="utf-8"))
                self._loaded = True
                return self._config
            except json.JSONDecodeError:
                pass

        # 回退：读旧 config.yaml
        if _OLD_CONFIG_YAML.exists():
            try:
                with open(_OLD_CONFIG_YAML, encoding="utf-8") as f:
                    old = yaml.safe_load(f) or {}
                self._config = self._merge_with_defaults(old)
                self._loaded = True
                return self._config
            except (yaml.YAMLError, OSError):
                pass

        self._config = dict(_DEFAULT_CONFIG)
        self._loaded = True
        return self._config

    def save(self, config: dict[str, Any] | None = None) -> None:
        """原子写入 config.json。"""
        if config is not None:
            self._config = config
        self._config_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self._config, ensure_ascii=False, indent=2)
        fd, tmp_path = tempfile.mkstemp(dir=self._config_dir, suffix=".tmp", prefix=".config_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp_path, self._config_file)
        except BaseException:
            os.unlink(tmp_path)
            raise

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔的嵌套键（如 'llm.model'）。"""
        if not self._loaded:
            self.load()
        obj: Any = self._config
        for part in key.split("."):
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                return default
            if obj is None:
                return default
        return obj

    def set(self, key: str, value: Any) -> None:
        """设置配置值，支持点号分隔的嵌套键。"""
        if not self._loaded:
            self.load()
        parts = key.split(".")
        obj = self._config
        for part in parts[:-1]:
            if part not in obj or not isinstance(obj[part], dict):
                obj[part] = {}
            obj = obj[part]
        obj[parts[-1]] = value

    def is_first_run(self) -> bool:
        """是否首次运行（config.json 和 .env 都不存在）。"""
        return not self._config_file.exists() and not self._env_file.exists()

    def ensure_env_loaded(self) -> None:
        """确保 .env 文件中的环境变量已加载到 os.environ。"""
        self._load_env()

    def get_env(self, key: str, default: str = "") -> str:
        """获取 .env 中的环境变量值。"""
        env = self._load_env()
        return env.get(key, default)

    def set_env(self, key: str, value: str) -> None:
        """设置 .env 中的环境变量值（原子写入）。"""
        env = self._load_env()
        env[key] = value
        self._env_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{k}={v}" for k, v in env.items()]
        content = "\n".join(lines) + "\n"
        fd, tmp_path = tempfile.mkstemp(
            dir=self._env_file.parent, suffix=".tmp", prefix=".env_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, self._env_file)
        except BaseException:
            os.unlink(tmp_path)
            raise
        os.environ[key] = value

    def validate(self) -> list[str]:
        """校验配置，返回错误列表。空列表表示通过。"""
        if not self._loaded:
            self.load()
        errors: list[str] = []

        # 必须字段
        vault_path = self.get("obsidian.vault_path", "")
        if not vault_path:
            errors.append("obsidian.vault_path 未设置")
        else:
            vp = Path(vault_path)
            if not vp.parent.exists():
                errors.append(f"obsidian.vault_path 父目录不存在: {vault_path}")
            elif not os.access(vp.parent, os.W_OK):
                errors.append(f"obsidian.vault_path 父目录不可写: {vault_path}")

        wewe_url = self.get("wewe_rss.base_url", "")
        if not wewe_url:
            errors.append("wewe_rss.base_url 未设置")

        # .env 必须字段
        env = self._load_env()
        if not env.get("DEEPSEEK_API_KEY"):
            errors.append("DEEPSEEK_API_KEY 未设置（在 .env 中）")

        return errors

    # -- 迁移 ---------------------------------------------------------------

    def migrate_from_old(self) -> MigrationReport:
        """从旧配置迁移。支持幂等性（多次运行结果一致）。

        合并策略：已有 config.json 为基准，补充旧 yaml 中有而 config.json 中没有的字段。
        """
        report = MigrationReport(success=True)

        # 确保目录存在
        self._config_dir.mkdir(parents=True, exist_ok=True)

        # 先加载已有 config.json（如果存在）
        existing: dict[str, Any] = {}
        if self._config_file.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                existing = json.loads(self._config_file.read_text(encoding="utf-8"))

        # 备份旧配置
        if _OLD_CONFIG_YAML.exists():
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = _OLD_CONFIG_YAML.with_suffix(f".yaml.bak.{ts}")
            shutil.copy2(_OLD_CONFIG_YAML, backup)
            report.backup_path = backup
            report.migrated_items.append(f"备份 config.yaml → {backup.name}")

        # 加载旧配置
        old_config: dict[str, Any] = {}
        if _OLD_CONFIG_YAML.exists():
            try:
                with open(_OLD_CONFIG_YAML, encoding="utf-8") as f:
                    old_config = yaml.safe_load(f) or {}
            except (yaml.YAMLError, OSError) as e:
                report.errors.append(f"读取旧 config.yaml 失败: {e}")

        # 合并：defaults + old + existing（existing 优先）
        merged = self._merge_with_defaults(old_config)
        for key, value in existing.items():
            if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value

        # 迁移 categories
        categories = old_config.get("categories", [])
        if categories:
            merged["categories"] = categories
            report.migrated_items.append(f"迁移 {len(categories)} 个分类")

        # 迁移 .env（复制敏感信息到新位置）
        if _OLD_ENV_FILE.exists() and not self._env_file.exists():
            try:
                shutil.copy2(_OLD_ENV_FILE, self._env_file)
                report.migrated_items.append("迁移 .env")
            except OSError as e:
                report.errors.append(f"迁移 .env 失败: {e}")

        # 保存合并后的配置
        self._config = merged
        self.save()
        report.migrated_items.append("生成 config.json")

        report.success = len(report.errors) == 0
        self._loaded = True
        return report

    # -- 连通性测试 ----------------------------------------------------------

    def test_llm_connection(self) -> ConnectionTestResult:
        """测试 LLM API 连通性。"""
        env = self._load_env()
        api_key = env.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            return ConnectionTestResult(
                success=False, latency_ms=0, message="DEEPSEEK_API_KEY 未设置"
            )

        base_url = self.get("llm.base_url", "https://api.deepseek.com")
        model = self.get("llm.model", "deepseek-chat")
        return self._test_openai_compat_api(
            base_url=base_url,
            api_key=api_key,
            model=model,
            label="LLM",
        )

    def test_vision_connection(self) -> ConnectionTestResult:
        """测试 Vision API 连通性。"""
        env = self._load_env()
        api_key = env.get("VISION_API_KEY", "")
        if not api_key:
            return ConnectionTestResult(
                success=False, latency_ms=0, message="VISION_API_KEY 未设置"
            )

        base_url = self.get("vision.base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        model = self.get("vision.model", "qwen-vl-plus")
        return self._test_openai_compat_api(
            base_url=base_url,
            api_key=api_key,
            model=model,
            label="Vision",
        )

    def test_wewe_rss_connection(self) -> ConnectionTestResult:
        """测试 WeWe RSS 连通性。"""
        base_url = self.get("wewe_rss.base_url", "http://localhost:4000")
        url = f"{base_url}/feeds/all.json"
        start = time.monotonic()
        try:
            resp = requests.get(url, timeout=5)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return ConnectionTestResult(
                    success=True,
                    latency_ms=round(elapsed, 1),
                    message="WeWe RSS 可达",
                )
            return ConnectionTestResult(
                success=False,
                latency_ms=round(elapsed, 1),
                message=f"HTTP {resp.status_code}",
            )
        except requests.RequestException as e:
            elapsed = (time.monotonic() - start) * 1000
            return ConnectionTestResult(
                success=False,
                latency_ms=round(elapsed, 1),
                message=f"连接失败: {e}",
            )

    def test_all_connections(self) -> dict[str, ConnectionTestResult]:
        """测试所有连通性。"""
        return {
            "llm": self.test_llm_connection(),
            "vision": self.test_vision_connection(),
            "wewe_rss": self.test_wewe_rss_connection(),
        }

    # -- 内部方法 -----------------------------------------------------------

    def _load_env(self) -> dict[str, str]:
        """加载 .env 文件（合并旧位置和新位置，新位置覆盖同名键），并设置到 os.environ。"""
        env: dict[str, str] = {}
        for path in [_OLD_ENV_FILE, self._env_file]:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                        value = value[1:-1]
                    env[key.strip()] = value
        # 同步到 os.environ，确保依赖 os.environ 的模块（如 load_vision_config）正常工作
        for k, v in env.items():
            if k not in os.environ:
                os.environ[k] = v
        return env

    def _merge_with_defaults(self, old: dict[str, Any]) -> dict[str, Any]:
        """将旧配置合并到默认配置模板（新配置优先，补充旧配置中有而新配置中没有的字段）。"""
        result: dict[str, Any] = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
        for key, value in old.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = {**result[key], **value}
            elif key not in result:
                result[key] = value
        return result

    def _test_openai_compat_api(
        self,
        base_url: str,
        api_key: str,
        model: str,
        label: str,
    ) -> ConnectionTestResult:
        """测试 OpenAI 兼容 API 的 /models 端点。"""
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}
        start = time.monotonic()
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return ConnectionTestResult(
                    success=True,
                    latency_ms=round(elapsed, 1),
                    message=f"{label} API 可达",
                    details={"model": model},
                )
            return ConnectionTestResult(
                success=False,
                latency_ms=round(elapsed, 1),
                message=f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
        except requests.RequestException as e:
            elapsed = (time.monotonic() - start) * 1000
            return ConnectionTestResult(
                success=False,
                latency_ms=round(elapsed, 1),
                message=f"连接失败: {e}",
            )
