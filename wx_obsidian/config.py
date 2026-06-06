"""配置与持久化：.env 加载、config.yaml、processed.json、Skill 文件。"""

from __future__ import annotations

import functools
import json
import os
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
PROCESSED_FILE = SCRIPT_DIR / "processed.json"
MAX_ARTICLE_LENGTH = 15000
MAX_PROMPT_CONTENT = 10000
SUB_TOPIC_THRESHOLD = 3

VISION_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
VISION_DEFAULT_MODEL = "qwen-vl-plus"
VISION_DEFAULT_CONCURRENCY = 10
VISION_DEFAULT_TIMEOUT = 120
VISION_DEFAULT_MAX_RETRIES = 2

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


def load_config() -> dict[str, Any]:
    """加载 config.yaml 配置。"""
    with open(SCRIPT_DIR / "config.yaml", encoding="utf-8") as f:
        result: dict[str, Any] = yaml.safe_load(f)
        return result


# ---------------------------------------------------------------------------
# processed.json
# ---------------------------------------------------------------------------


def load_processed() -> dict[str, Any]:
    """加载已处理文章记录。"""
    if PROCESSED_FILE.exists():
        try:
            result: dict[str, Any] = json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
            return result
        except json.JSONDecodeError as e:
            print(f"警告: processed.json 解析失败 ({e})，将重新开始")
            return {}
    return {}


def save_processed(processed: dict[str, Any]) -> None:
    """保存已处理文章记录（原子写入，防止进程中断导致文件损坏）。"""
    data = json.dumps(processed, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=PROCESSED_FILE.parent, suffix=".tmp", prefix=".processed_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_path, PROCESSED_FILE)
    except BaseException:
        os.unlink(tmp_path)
        raise


def load_last_fetch_date() -> str | None:
    """加载上次抓取日期。"""
    processed = load_processed()
    return processed.get("last_fetch_date")


def save_last_fetch_date(date_str: str, processed: dict[str, Any] | None = None) -> None:
    """保存本次抓取日期。"""
    if processed is None:
        processed = load_processed()
    processed["last_fetch_date"] = date_str
    save_processed(processed)


def load_max_workers() -> int:
    """加载并行度配置。"""
    config = load_config()
    try:
        value = int(config.get("max_workers", 5))
        return max(1, min(value, 32))
    except (ValueError, TypeError):
        print("警告: max_workers 配置无效，使用默认值 5")
        return 5


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


@functools.cache
def load_vision_config() -> dict[str, Any] | None:
    """加载多模态 Vision API 配置。VISION_API_KEY 未设置时返回 None。"""
    api_key = os.environ.get("VISION_API_KEY", "")
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": os.environ.get("VISION_BASE_URL", VISION_DEFAULT_BASE_URL),
        "model": os.environ.get("VISION_MODEL_NAME", VISION_DEFAULT_MODEL),
        "max_concurrency": int(
            os.environ.get("MAX_VISION_CONCURRENCY", VISION_DEFAULT_CONCURRENCY)
        ),
        "timeout": int(os.environ.get("VISION_TIMEOUT", VISION_DEFAULT_TIMEOUT)),
        "max_retries": int(os.environ.get("VISION_MAX_RETRIES", VISION_DEFAULT_MAX_RETRIES)),
    }
