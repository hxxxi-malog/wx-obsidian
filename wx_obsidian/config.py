"""配置与持久化：.env 加载、config.yaml、processed.json、Skill 文件。"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.parent
SKILLS_DIR = SCRIPT_DIR / "skills"
PROMPTS_DIR = SCRIPT_DIR / "prompts"
PROCESSED_FILE = Path.home() / ".wx-obsidian" / "processed.json"
MAX_ARTICLE_LENGTH = 100000
MAX_PROMPT_CONTENT = 100000
SUB_TOPIC_THRESHOLD = 3

VISION_DEFAULT_BASE_URL = "https://api.deepseek.com"
# deepseek-flash 支持图像输入；deepseek-v4-pro 不支持（实测会回答「看不到图片」）
VISION_DEFAULT_MODEL = "deepseek-flash"
VISION_DEFAULT_CONCURRENCY = 10
VISION_DEFAULT_TIMEOUT = 120
VISION_DEFAULT_MAX_RETRIES = 2
# 推理型多模态模型需要更大的输出预算，1024 会让正文 JSON 被截断
VISION_DEFAULT_MAX_TOKENS = 3072


def sanitize_path_segment(text: str, max_len: int = 100) -> str:
    """清理文本中不能用于文件路径的字符，按 AGENTS.md 编码红线第 2 条。

    先将弯引号归一化为直引号（防止 Obsidian 断链），全角冒号归一化为 ASCII 冒号，
    再将 Unicode 空格字符（U+00A0 等）归一化为普通空格，
    最后将 ``<>:"/\\|?*[]`` 替换为下划线。
    """
    normalized = text.replace("“", '"').replace("”", '"').replace("：", ":")
    # Unicode 空格归一化：U+00A0, U+2000-U+200A, U+202F, U+205F, U+3000 → 空格
    # Obsidian 解析 wikilink 时会做此归一化，但 macOS APFS 区分这些字符，
    # 因此必须在文件名层面统一，否则链接无法解析。
    normalized = re.sub(r"[\xa0 -   　]", " ", normalized)
    return re.sub(r'[<>:"/\\|?*\[\]]', "_", normalized)[:max_len]


# ---------------------------------------------------------------------------
# 环境变量布尔值解析
# ---------------------------------------------------------------------------

_TRUTHY = frozenset({"1", "true", "yes", "on", "y", "t"})
_FALSY = frozenset({"", "0", "false", "no", "off", "n", "f"})


def env_flag(name: str, default: bool = False) -> bool:
    """把环境变量按布尔值解析，而不是按「是否存在」判断。

    ``os.environ.get(name)`` 返回非空字符串恒为真，而 `.env` 模板里写的是
    ``DEBUG=0``——存在性判断会把「关闭」当成「开启」。无法识别的取值按
    ``default`` 处理（fail-closed），并留一条告警，避免拼错时静默变成开启。
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    logger.warning("环境变量 %s=%r 不是合法布尔值，按 %s 处理", name, raw, default)
    return default


# ---------------------------------------------------------------------------
# .env 加载（不覆盖已有的环境变量）
# ---------------------------------------------------------------------------

_ENV_FILE = SCRIPT_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        _key, _value = _key.strip(), _value.strip()
        if _key not in os.environ:
            os.environ[_key] = _value


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------


@functools.cache
def load_config() -> dict[str, Any]:
    """加载 config.yaml 配置（已废弃，主流程使用 ConfigManager）。

    .. deprecated::
        请使用 ``config_manager.ConfigManager`` 读取 ``~/.wx-obsidian/config.json``。
    """
    config_path = SCRIPT_DIR / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            result = yaml.safe_load(f)
            return result if isinstance(result, dict) else {}
    except (yaml.YAMLError, OSError):
        return {}


# ---------------------------------------------------------------------------
# processed.json
# ---------------------------------------------------------------------------


def resolve_source_base_url(configured: str = "") -> str:
    """解析最终生效的数据源地址（环境变量 `WX_SOURCE_BASE_URL` 优先）。

    和 `resolve_source_type` 的 `WX_SOURCE` 覆盖是同一套路，但这条对容器部署是**必需**的：
    容器里数据源是服务名（`http://we-mp-rss:8001`），宿主上是 `localhost:8001`，
    而 `config.json` 只有一份（`processed.json` 必须共用，否则两边会各处理一遍
    同一篇文章），所以地址这类随环境变化的值只能靠环境变量区分。

    放在 config.py（最底层）而不是 sources/factory.py：连通性探活
    （config_manager）也要用同一个值，放上层会引出循环依赖。
    """
    env_value = os.environ.get("WX_SOURCE_BASE_URL", "").strip()
    if env_value:
        return env_value.rstrip("/")
    return str(configured or "").strip().rstrip("/")


def load_processed(path: Path | None = None) -> dict[str, Any]:
    """加载已处理文章记录。

    Args:
        path: 指定记录文件；省略时用默认的 ~/.wx-obsidian/processed.json。
    """
    target = path or PROCESSED_FILE
    if target.exists():
        try:
            result: dict[str, Any] = json.loads(target.read_text(encoding="utf-8"))
            return result
        except (json.JSONDecodeError, OSError) as e:
            print(f"警告: processed.json 解析失败 ({e})，将重新开始")
            return {}
    return {}


def atomic_write(path: Path, content: str) -> None:
    """原子写入文件：先写临时文件，再 os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp", prefix=f".{path.stem}_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


# 账本「灾难性缩水」的判定阈值：既有记录够多、新记录却只剩一小撮时，
# 几乎一定是调用方传错了数据（例如测试把 fixture 写进了真实路径）。
_SHRINK_GUARD_MIN_EXISTING = 50
_SHRINK_GUARD_RATIO = 0.2


def _record_count(data: dict[str, Any]) -> int:
    """数出真正的文章记录条数（排除 last_fetch_date 这类顶层标量）。"""
    return sum(1 for value in data.values() if isinstance(value, dict))


def save_processed(
    processed: dict[str, Any], path: Path | None = None, *, force: bool = False
) -> None:
    """保存已处理文章记录（原子写入，防止进程中断导致文件损坏）。

    Args:
        processed: 完整记录字典。
        path: 指定记录文件；省略时用默认的 ~/.wx-obsidian/processed.json。
        force: 跳过「灾难性缩水」保护。恢复脚本/手工重建账本时才用。

    Raises:
        ValueError: 新记录会让账本从大量条目塌缩到不足 20%（疑似传错数据）。
            这条护栏来自一次真实事故：某个测试走了 cascade_delete →
            本函数（默认路径），把 3 条假 fixture 记录写进了用户 373 条的账本里。
    """
    target = path or PROCESSED_FILE
    if not force and target.exists():
        try:
            existing = _record_count(json.loads(target.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            existing = 0
        incoming = _record_count(processed)
        if existing >= _SHRINK_GUARD_MIN_EXISTING and incoming < existing * _SHRINK_GUARD_RATIO:
            raise ValueError(
                f"拒绝写入 {target}：账本将从 {existing} 条塌缩到 {incoming} 条。"
                f"这几乎一定是调用方传错了数据；确实要重建请显式传 force=True。"
            )
    data = json.dumps(processed, ensure_ascii=False, indent=2)
    atomic_write(target, data)


def load_max_workers() -> int:
    """加载并行度配置。"""
    config = load_config()
    try:
        value = int(config.get("max_workers", 5))
        return max(1, min(value, 32))
    except (ValueError, TypeError):
        print("警告: max_workers 配置无效，使用默认值 5")
        return 5


def load_similarity_db_path() -> Path:
    """加载相似度数据库路径配置。"""
    config = load_config()
    raw = config.get("similarity_db_path")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".wx-obsidian" / "similarity.sqlite"


# ---------------------------------------------------------------------------
# Skill 文件
# ---------------------------------------------------------------------------


@functools.cache
def load_skill(name: str) -> str:
    """加载 skill 文件内容，去掉 YAML frontmatter。"""
    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        return ""
    text = skill_file.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else ""


# ---------------------------------------------------------------------------
# Vision 配置
# ---------------------------------------------------------------------------


def load_vision_config(config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """加载多模态 Vision 配置。

    取值优先级：`config["vision"]`（ConfigManager.resolve_vision_channel() 的产物）
    > os.environ。`vision.enabled=False` 或拿不到 api_key 时返回 None（跳过视觉环节）。

    Args:
        config: 配置字典（来自 ConfigManager / orchestrator）。
    """
    raw = config.get("vision") if config else None
    vision_cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}

    if vision_cfg.get("enabled") is False:
        logger.info("Vision 已按配置禁用，跳过")
        return None

    api_key = str(vision_cfg.get("api_key") or os.environ.get("VISION_API_KEY", ""))
    if not api_key:
        return None

    return {
        "api_key": api_key,
        "base_url": str(
            vision_cfg.get("base_url") or os.environ.get("VISION_BASE_URL", VISION_DEFAULT_BASE_URL)
        ),
        "model": str(
            vision_cfg.get("model") or os.environ.get("VISION_MODEL_NAME", VISION_DEFAULT_MODEL)
        ),
        "max_concurrency": int(
            vision_cfg.get("max_concurrency")
            or os.environ.get("MAX_VISION_CONCURRENCY", str(VISION_DEFAULT_CONCURRENCY))
        ),
        "timeout": int(
            vision_cfg.get("timeout")
            or os.environ.get("VISION_TIMEOUT", str(VISION_DEFAULT_TIMEOUT))
        ),
        "max_retries": int(os.environ.get("VISION_MAX_RETRIES", VISION_DEFAULT_MAX_RETRIES)),
        "max_tokens": int(vision_cfg.get("max_tokens") or VISION_DEFAULT_MAX_TOKENS),
        "inline_images": bool(vision_cfg.get("inline_images", True)),
    }
