"""配置与持久化：.env 加载、config.yaml、processed.json、Skill 文件。"""

from __future__ import annotations

import functools
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.parent
SKILLS_DIR = SCRIPT_DIR / "skills"
PROMPTS_DIR = SCRIPT_DIR / "prompts"
PROCESSED_FILE = Path.home() / ".wx-obsidian" / "processed.json"
MAX_ARTICLE_LENGTH = 15000
MAX_PROMPT_CONTENT = 10000
SUB_TOPIC_THRESHOLD = 3

VISION_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VISION_DEFAULT_MODEL = "qwen-vl-plus"
VISION_DEFAULT_CONCURRENCY = 10
VISION_DEFAULT_TIMEOUT = 120
VISION_DEFAULT_MAX_RETRIES = 2


def sanitize_path_segment(text: str, max_len: int = 100) -> str:
    """清理文本中不能用于文件路径的字符，按 CLAUDE.md 编码红线第 2 条。

    先将弯引号归一化为直引号（防止 Obsidian 断链），再将 Unicode 空格字符
    （U+00A0 等）归一化为普通空格，最后将 ``<>:"/\\|?*[]`` 替换为下划线。
    """
    normalized = text.replace("“", '"').replace("”", '"')
    # Unicode 空格归一化：U+00A0, U+2000-U+200A, U+202F, U+205F, U+3000 → 空格
    # Obsidian 解析 wikilink 时会做此归一化，但 macOS APFS 区分这些字符，
    # 因此必须在文件名层面统一，否则链接无法解析。
    normalized = re.sub(r"[\xa0 -   　]", " ", normalized)
    return re.sub(r'[<>:"/\\|?*\[\]]', "_", normalized)[:max_len]


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


def load_processed() -> dict[str, Any]:
    """加载已处理文章记录。"""
    if PROCESSED_FILE.exists():
        try:
            result: dict[str, Any] = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
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


def save_processed(processed: dict[str, Any]) -> None:
    """保存已处理文章记录（原子写入，防止进程中断导致文件损坏）。"""
    data = json.dumps(processed, ensure_ascii=False, indent=2)
    atomic_write(PROCESSED_FILE, data)


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
    """加载多模态 Vision API 配置。VISION_API_KEY 未设置时返回 None。

    Args:
        config: 配置字典（来自 ConfigManager），优先从中读取 vision.base_url、
            vision.model 等。未提供时回退到 os.environ。
    """
    api_key = os.environ.get("VISION_API_KEY", "")
    if not api_key:
        return None

    vision_cfg = config.get("vision", {}) if config else {}

    return {
        "api_key": api_key,
        "base_url": vision_cfg.get(
            "base_url", os.environ.get("VISION_BASE_URL", VISION_DEFAULT_BASE_URL)
        ),
        "model": vision_cfg.get("model", os.environ.get("VISION_MODEL_NAME", VISION_DEFAULT_MODEL)),
        "max_concurrency": int(
            vision_cfg.get(
                "max_concurrency",
                os.environ.get("MAX_VISION_CONCURRENCY", VISION_DEFAULT_CONCURRENCY),
            )
        ),
        "timeout": int(
            vision_cfg.get("timeout", os.environ.get("VISION_TIMEOUT", VISION_DEFAULT_TIMEOUT))
        ),
        "max_retries": int(os.environ.get("VISION_MAX_RETRIES", VISION_DEFAULT_MAX_RETRIES)),
    }
