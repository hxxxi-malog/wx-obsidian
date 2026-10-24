"""配置管理：读写 ~/.wx-obsidian/config.json，迁移旧配置，连通性测试。"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
import yaml

from wx_obsidian.config import resolve_source_base_url
from wx_obsidian.models import ConnectionTestResult, MigrationReport

logger = logging.getLogger(__name__)

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

# 已随功能删除的配置段：加载时忽略并剔除，避免旧 config.json 里一直留着死键
_REMOVED_SECTIONS = ("scheduler",)

# 合法的数据源类型。schema 由本模块定义；具体实现与解析见 sources/factory.py
# （此处不 import sources，保持配置层不依赖上层模块）。
_VALID_SOURCE_TYPES = ("we_mp_rss",)

# 内置渠道模板：只要在对应段落的 active 里写上名字即可切换。
# 用户也可以在 config.json 的 providers 下自行新增任意兼容 OpenAI 协议的渠道。
_BUILTIN_CHANNELS: dict[str, dict[str, dict[str, Any]]] = {
    "llm": {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
    },
    "vision": {
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
        },
        "dashscope": {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen-vl-plus",
            "api_key_env": "VISION_API_KEY",
        },
    },
}

_DEFAULT_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "llm": {
        # active 指向 providers 里的渠道名；切换渠道只改这一个字段
        "active": "deepseek",
        "providers": {},
        "max_tokens": 4096,
        "temperature": 0.7,
    },
    "vision": {
        "enabled": True,
        # 默认走 DeepSeek 的多模态模型（deepseek-flash 支持图像输入；deepseek-v4-pro 不支持）
        "active": "deepseek",
        "providers": {},
        "max_concurrency": 10,
        "timeout": 120,
        # 推理型多模态模型需要更大的输出预算，1024 会导致正文 JSON 被截断
        "max_tokens": 3072,
        # 微信图有防盗链，服务端抓不到，改为本地下载后内联成 data URL
        "inline_images": True,
    },
    "obsidian": {
        "vault_path": "",
        "articles_dir": "公众号文章",
    },
    # 文章数据源：we-mp-rss。也可用环境变量 WX_SOURCE 临时覆盖，无需改配置。
    "source": {
        "type": "we_mp_rss",
        "base_url": "http://localhost:8001",
        "timeout": 15,
        "token": "",
        # 上游只在收到「更新」请求时采集新文章，所以 Web 服务按间隔主动触发；
        # 关闭后只能靠扫码登录成功或手动点「立即采集」触发。
        "auto_sync": True,
        "sync_interval_minutes": 30,
    },
    "fetch": {
        "max_days": 7,
        "max_workers": 5,
        # 抓取窗口（篇）。we-mp-rss 的 /feed/*.json 连正文一起返回，
        # 调大会显著增加传输量；调小则可能漏掉窗口外的文章。
        "feed_limit": 300,
    },
    # Web 模式（`process_articles.py web` / 容器）
    # 注意：host 与 allow_remote 是**启动期**设置（改了要重启），
    # 容器部署通常直接用环境变量 WX_WEB_HOST / WX_ALLOW_REMOTE 覆盖。
    "web": {
        "port": 8765,
        "host": "127.0.0.1",
        "allow_remote": False,
        "open_browser": True,
        "log_level": "INFO",
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

    @property
    def config_file(self) -> Path:
        """config.json 的实际路径（尊重构造时传入的 config_dir）。"""
        return self._config_file

    # -- 读写 ---------------------------------------------------------------

    def load(self) -> dict[str, Any]:
        """加载配置。优先读 config.json，回退到旧 config.yaml。

        加载后会补齐缺失的默认字段，保证旧配置（例如只有 wewe_rss、
        没有 source 的 config.json）也能获得新数据源配置段。
        """
        if self._config_file.exists():
            try:
                loaded = json.loads(self._config_file.read_text(encoding="utf-8"))
                self._config = self._merge_with_defaults(loaded)
                self._migrate_channel_config()
                self._prune_removed_sections()
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
                self._migrate_channel_config()
                self._prune_removed_sections()
                self._loaded = True
                return self._config
            except (yaml.YAMLError, OSError):
                pass

        self._config = json.loads(json.dumps(_DEFAULT_CONFIG))
        self._migrate_channel_config()
        self._prune_removed_sections()
        self._loaded = True
        return self._config

    def _prune_removed_sections(self) -> None:
        """删掉已被移除的功能遗留的配置段（幂等）。

        `_merge_with_defaults` 会保留用户配置里默认模板没有的键，所以功能删掉后
        旧 config.json 里的残留段落会一直留着。`scheduler` 曾定义
        `fetch_cron` / `keepalive_interval_days`，但定时任务功能已整体移除。
        """
        for section in _REMOVED_SECTIONS:
            if self._config.pop(section, None) is not None:
                logger.info("配置段 %s 已随功能移除，忽略", section)

    def _migrate_channel_config(self) -> None:
        """把旧的扁平 provider 配置整理为 `active` + `providers` 渠道结构（幂等）。

        兼容两种旧写法：
        - `llm.provider` / `llm.base_url` / `llm.model`（扁平）
        - 缺少 `active`

        迁移后扁平字段会被移除，避免与新结构并存造成歧义；
        旧值会保留到以 `provider` 命名的渠道里，随时可切回去。
        """
        for section, builtin in _BUILTIN_CHANNELS.items():
            cfg = self._config.get(section)
            if not isinstance(cfg, dict):
                cfg = {}
                self._config[section] = cfg

            existing = cfg.get("providers")
            providers: dict[str, dict[str, Any]] = (
                dict(existing) if isinstance(existing, dict) else {}
            )

            # 1) 铺内置模板，保证常见渠道开箱可切换（用户已写的字段优先）
            for name, template in builtin.items():
                merged = dict(template)
                current = providers.get(name)
                if isinstance(current, dict):
                    merged.update(current)
                providers[name] = merged

            # 2) 旧扁平字段归入以其 provider 命名的渠道
            legacy_provider = str(cfg.get("provider") or "").strip()
            flat = {
                key: cfg[key]
                for key in ("base_url", "model", "api_key_env")
                if isinstance(cfg.get(key), str) and cfg.get(key)
            }
            if flat:
                name = legacy_provider or "legacy"
                merged = dict(builtin.get(name, {}))
                current = providers.get(name)
                if isinstance(current, dict):
                    merged.update(current)
                merged.update(flat)
                providers[name] = merged

            # 3) 确定 active：显式指定 > 旧 provider > 内置第一个
            active = str(cfg.get("active") or "").strip()
            if not active or active not in providers:
                active = legacy_provider if legacy_provider in providers else next(iter(builtin))
            cfg["active"] = active
            cfg["providers"] = providers

            # 4) 清理扁平字段
            for key in ("provider", "base_url", "model", "api_key_env"):
                cfg.pop(key, None)

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
        fd, tmp_path = tempfile.mkstemp(dir=self._env_file.parent, suffix=".tmp", prefix=".env_")
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

        source_type = self.get("source.type", "")
        source_url = self.get("source.base_url", "")
        if not source_type:
            errors.append("source.type 未设置")
        elif source_type not in _VALID_SOURCE_TYPES:
            errors.append(
                f"source.type 取值非法: {source_type}（应为 {' 或 '.join(_VALID_SOURCE_TYPES)}）"
            )
        if not source_url:
            errors.append("source.base_url 未设置")

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
        self._prune_removed_sections()
        self.save()
        report.migrated_items.append("生成 config.json")

        report.success = len(report.errors) == 0
        self._loaded = True
        return report

    # -- 连通性测试 ----------------------------------------------------------

    def test_llm_connection(self) -> ConnectionTestResult:
        """测试当前生效 LLM 渠道的连通性。"""
        channel = self.resolve_llm_channel()
        api_key = str(channel.get("api_key") or "")
        if not api_key:
            return ConnectionTestResult(
                success=False,
                latency_ms=0,
                message=f"渠道 {channel['provider']} 的 API Key 未设置",
            )

        return self._test_openai_compat_api(
            base_url=str(channel["base_url"]),
            api_key=api_key,
            model=str(channel["model"]),
            label="LLM",
        )

    def test_vision_connection(self) -> ConnectionTestResult:
        """测试当前生效 Vision 渠道的连通性。"""
        channel = self.resolve_vision_channel()
        api_key = str(channel.get("api_key") or "")
        if not api_key:
            return ConnectionTestResult(
                success=False,
                latency_ms=0,
                message=f"渠道 {channel['provider']} 的 API Key 未设置",
            )

        return self._test_openai_compat_api(
            base_url=str(channel["base_url"]),
            api_key=api_key,
            model=str(channel["model"]),
            label="Vision",
        )

    def test_source_connection(self) -> ConnectionTestResult:
        """测试文章数据源（we-mp-rss）的连通性。"""
        # 地址必须与适配器用同一个解析函数：否则容器里适配器走服务名、
        # 探活却去连 localhost，健康面板会误报「连接失败」
        base_url = resolve_source_base_url(self.get("source.base_url", "")) or (
            "http://localhost:8001"
        )
        # /feed/all.json 是无需鉴权的只读接口，适合做探活
        return self._test_endpoint(f"{base_url}/feed/all.json?limit=1", "we-mp-rss")

    def _test_endpoint(self, url: str, label: str) -> ConnectionTestResult:
        """GET 探活并返回统一的连通性结果。"""
        start = time.monotonic()
        try:
            resp = requests.get(url, timeout=5)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code == 200:
                return ConnectionTestResult(
                    success=True,
                    latency_ms=round(elapsed, 1),
                    message=f"{label} 可达",
                )
            return ConnectionTestResult(
                success=False,
                latency_ms=round(elapsed, 1),
                message=f"{label} HTTP {resp.status_code}",
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
            "source": self.test_source_connection(),
        }

    # -- 模型渠道（LLM / Vision） -------------------------------------------

    def _channel_api_key(self, channel: dict[str, Any], default_env: str = "") -> str:
        """取渠道 API Key：优先渠道内联的 api_key，其次其声明的 api_key_env。

        api_key_env 同时支持 `.env` 文件与已导出的 shell 环境变量
        （`.env` 优先，与 ConfigManager 一贯的覆盖顺序一致）。
        """
        inline = channel.get("api_key")
        if isinstance(inline, str) and inline.strip():
            return inline.strip()
        env_name = str(channel.get("api_key_env") or default_env or "").strip()
        if not env_name:
            return ""
        # _load_env() 会把 .env 同步进 os.environ，故统一从 os.environ 取
        self._load_env()
        return os.environ.get(env_name, "")

    def channel_names(self, section: str) -> list[str]:
        """列出某段落（llm / vision）已配置的渠道名。"""
        if not self._loaded:
            self.load()
        providers = self.get(f"{section}.providers", {})
        return sorted(providers) if isinstance(providers, dict) else []

    def set_active_channel(self, section: str, name: str) -> bool:
        """切换某段落的生效渠道。渠道不存在时返回 False，不改动配置。"""
        if name not in self.channel_names(section):
            return False
        self.set(f"{section}.active", name)
        return True

    def resolve_llm_channel(self) -> dict[str, Any]:
        """解析当前生效的语言模型渠道（已含 api_key），供 llm.py 直接使用。"""
        if not self._loaded:
            self.load()
        cfg = self.get("llm", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        active = str(cfg.get("active") or "deepseek")
        providers = cfg.get("providers")
        channel = providers.get(active) if isinstance(providers, dict) else None
        channel = channel if isinstance(channel, dict) else {}

        return {
            "provider": active,
            "base_url": str(
                channel.get("base_url")
                or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            ),
            "model": str(channel.get("model") or os.environ.get("MODEL_NAME", "deepseek-flash")),
            "api_key": self._channel_api_key(channel, "DEEPSEEK_API_KEY"),
            "max_tokens": int(cfg.get("max_tokens", 4096) or 4096),
            "temperature": float(cfg.get("temperature", 0.7) or 0.7),
        }

    def resolve_vision_channel(self) -> dict[str, Any]:
        """解析当前生效的视觉模型渠道（已含 api_key）。

        deepseek 渠道通常复用语言模型的 Key，因此该渠道未显式配置 key 时
        回退到 DEEPSEEK_API_KEY。
        """
        if not self._loaded:
            self.load()
        cfg = self.get("vision", {})
        cfg = cfg if isinstance(cfg, dict) else {}
        active = str(cfg.get("active") or "deepseek")
        providers = cfg.get("providers")
        channel = providers.get(active) if isinstance(providers, dict) else None
        channel = channel if isinstance(channel, dict) else {}

        api_key = self._channel_api_key(channel, "VISION_API_KEY")
        if not api_key and active == "deepseek":
            api_key = self._channel_api_key({}, "DEEPSEEK_API_KEY")

        return {
            "provider": active,
            "enabled": bool(cfg.get("enabled", True)),
            "base_url": str(
                channel.get("base_url")
                or os.environ.get("VISION_BASE_URL", "https://api.deepseek.com")
            ),
            "model": str(
                channel.get("model") or os.environ.get("VISION_MODEL_NAME", "deepseek-flash")
            ),
            "api_key": api_key,
            "max_concurrency": int(cfg.get("max_concurrency", 10) or 10),
            "timeout": int(cfg.get("timeout", 120) or 120),
            "max_tokens": int(cfg.get("max_tokens", 3072) or 3072),
            "inline_images": bool(cfg.get("inline_images", True)),
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
        # 同步到 os.environ，确保依赖 os.environ 的模块（如 load_vision_config）正常工作。
        # 无条件写入：合并后的 env 已按优先级排列（新位置覆盖旧位置），
        # 若用 k not in os.environ 守卫，旧 .env 先加载的值会阻止新位置的值生效。
        for k, v in env.items():
            os.environ[k] = v
        return env

    def _merge_with_defaults(self, old: dict[str, Any]) -> dict[str, Any]:
        """将旧配置合并到默认配置模板（新配置优先，补充旧配置中有而新配置中没有的字段）。"""
        result: dict[str, Any] = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy
        for key, value in old.items():
            default = result.get(key)
            if isinstance(default, dict) and isinstance(value, dict):
                result[key] = {**default, **value}
            elif isinstance(default, dict):
                # 默认值是段落而用户给的不是 dict：保留默认段落，避免下游 .get() 崩溃
                continue
            else:
                # 默认值是标量（或默认模板中没有该键）：新配置优先
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
