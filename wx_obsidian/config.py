"""配置与持久化：.env 加载、config.yaml、processed.json、Skill 文件。"""

from __future__ import annotations

import functools
import json
import os
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
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# processed.json
# ---------------------------------------------------------------------------


def load_processed() -> dict[str, Any]:
    """加载已处理文章记录。"""
    if PROCESSED_FILE.exists():
        try:
            return json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"警告: processed.json 解析失败 ({e})，将重新开始")
            return {}
    return {}


def save_processed(processed: dict[str, Any]) -> None:
    """保存已处理文章记录。"""
    PROCESSED_FILE.write_text(
        json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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
# 知识库扫描
# ---------------------------------------------------------------------------


def scan_existing_content(
    vault_path: Path, articles_dir_name: str
) -> tuple[list[str], list[str]]:
    """扫描知识库中已有的文章和概念，用于相关主题关联。"""
    articles_base = vault_path / articles_dir_name
    existing_articles: list[str] = []
    existing_concepts: list[str] = []

    if articles_base.exists():
        for category_dir in articles_base.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue
            for md_file in category_dir.glob("*.md"):
                if md_file.name != "_MOC.md":
                    existing_articles.append(md_file.stem)

    concept_dir = articles_base / "概念"
    if concept_dir.exists():
        for md_file in concept_dir.glob("*.md"):
            if md_file.name != "_MOC.md":
                existing_concepts.append(md_file.stem)

    return existing_articles, existing_concepts
