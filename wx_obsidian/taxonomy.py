"""分类体系声明（`skills/classification/taxonomy.yaml`）的加载与主题词归一化。

为什么需要它：`maybe_create_subcategory` 只按「同一父分类下同一 sub_topic 达到阈值」
建二级目录，而 LLM 每次独立判断 category / sub_topic，于是同一主题会因
「中英混写」与「父分类抖动」被拆进多个目录（实测 Loop 主题曾散成 3 个目录 15 篇）。

本模块只解决前者（词形），归属锁定见 docs/design-docs/taxonomy/tasks.md 的长期项 L2。

设计约定（spec §4.2）：
- 只做已知别名的精确匹配（忽略大小写与空格），不做模糊匹配、不做翻译推断；
- 未命中别名时原样返回，新增主题不受阻；
- 声明文件缺失或损坏时**降级为恒等映射并记 WARNING**，绝不因为治理文件缺失而中断管线。
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

import yaml

from wx_obsidian.config import SKILLS_DIR

logger = logging.getLogger(__name__)

TAXONOMY_FILE = SKILLS_DIR / "classification" / "taxonomy.yaml"

#: 注入 prompt 的词表上限（spec §3 L3-3）：控制 prompt 体积
VOCABULARY_LIMIT = 40
VOCABULARY_CHAR_LIMIT = 1500


def _empty_taxonomy() -> dict[str, Any]:
    return {"version": 0, "aliases": {}, "sub_topics": []}


@functools.lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    """加载分类体系声明；任何异常都降级为空表（恒等映射）。

    Returns:
        `{"version": int, "aliases": dict[str, str], "sub_topics": list[dict[str, str]]}`

    Note:
        结果带缓存；测试或运行期改了文件需要 `load_taxonomy.cache_clear()`。
    """
    if not TAXONOMY_FILE.exists():
        logger.warning("分类体系声明不存在：%s（主题词将不做归一化）", TAXONOMY_FILE)
        return _empty_taxonomy()

    try:
        raw = yaml.safe_load(TAXONOMY_FILE.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
        logger.warning("分类体系声明读取失败，降级为不做归一化：%s", e)
        return _empty_taxonomy()

    if not isinstance(raw, dict):
        logger.warning("分类体系声明格式异常（应为映射），降级为不做归一化")
        return _empty_taxonomy()

    aliases: dict[str, str] = {}
    raw_aliases = raw.get("aliases")
    if isinstance(raw_aliases, dict):
        for key, value in raw_aliases.items():
            target = str(value).strip()
            if str(key).strip() and target:
                aliases[str(key)] = target
    elif raw_aliases is not None:
        logger.warning("aliases 应为映射，已忽略：%s", type(raw_aliases).__name__)

    sub_topics: list[dict[str, str]] = []
    raw_sub_topics = raw.get("sub_topics")
    if isinstance(raw_sub_topics, list):
        for item in raw_sub_topics:
            if isinstance(item, dict) and str(item.get("name") or "").strip():
                sub_topics.append(
                    {
                        "name": str(item["name"]).strip(),
                        "scope": str(item.get("scope") or "").strip(),
                    }
                )
    elif raw_sub_topics is not None:
        logger.warning("sub_topics 应为列表，已忽略：%s", type(raw_sub_topics).__name__)

    version = raw.get("version")
    return {
        "version": int(version) if isinstance(version, int) else 0,
        "aliases": aliases,
        "sub_topics": sub_topics,
    }


def _match_key(text: str) -> str:
    """别名匹配用的归一化键：去首尾空白、忽略大小写、去掉内部空格。"""
    return text.strip().casefold().replace(" ", "")


def canonical_sub_topic(name: str) -> str:
    """把 sub_topic 的别名统一成规范写法。

    未命中别名（或声明不可用）时原样返回去掉首尾空白的结果，因此新增主题不受阻。
    """
    text = (name or "").strip()
    if not text:
        return ""
    aliases: dict[str, str] = load_taxonomy()["aliases"]
    if not aliases:
        return text
    if text in aliases:
        return aliases[text]
    wanted = _match_key(text)
    for key, value in aliases.items():
        if _match_key(key) == wanted:
            return value
    return text


def sub_topic_vocabulary() -> list[tuple[str, str]]:
    """返回受控主题词表 `[(规范名, 覆盖范围), ...]`，受上限约束（spec §3 L3-3）。"""
    items: list[tuple[str, str]] = []
    used = 0
    for entry in load_taxonomy()["sub_topics"]:
        name = entry["name"]
        scope = entry["scope"]
        line_len = len(name) + len(scope)
        if len(items) >= VOCABULARY_LIMIT or used + line_len > VOCABULARY_CHAR_LIMIT:
            logger.debug("主题词表超出上限，已截断到 %d 条", len(items))
            break
        items.append((name, scope))
        used += line_len
    return items


def format_vocabulary() -> str:
    """把受控词表渲染成注入 prompt 的文本块；空表时返回空串（整段省略）。"""
    items = sub_topic_vocabulary()
    if not items:
        return ""
    lines = [f"- {name}：{scope}" if scope else f"- {name}" for name, scope in items]
    return "\n".join(lines)


def taxonomy_file() -> Path:
    """声明文件路径（测试与排错用）。"""
    return TAXONOMY_FILE
