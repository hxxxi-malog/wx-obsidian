"""文章列表投影：从 processed.json 读文章，复用既有的级联删除。

刻意不改 `output/cleanup.py`：Web 只是给 `cascade_delete` 换个调用方。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from wx_obsidian.config import load_processed
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.output.cleanup import cascade_delete

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500


def _iter_records(processed: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """过滤出真正的文章记录。

    processed.json 里还有 `last_fetch_date` 这类非 dict 的顶层键，历史版本也可能留下
    字符串记录，一并跳过。
    """
    return [
        (str(article_id), record)
        for article_id, record in processed.items()
        if isinstance(record, dict) and record.get("title")
    ]


def _project(article_id: str, record: dict[str, Any]) -> dict[str, Any]:
    """记录 → 前端 DTO。"""
    file_path = str(record.get("file") or "")
    return {
        "article_id": article_id,
        "title": str(record.get("title") or ""),
        "category": str(record.get("category") or ""),
        "sub_topic": str(record.get("sub_topic") or ""),
        "status": str(record.get("status") or ""),
        "file_path": file_path,
        "file_exists": bool(file_path) and Path(file_path).exists(),
        "processed_at": str(record.get("processed_at") or ""),
        "error": str(record.get("error") or record.get("reason") or ""),
        "retry_count": int(record.get("retry_count") or 0),
    }


def list_articles(
    *,
    query: str = "",
    category: str = "",
    status: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    processed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """文章列表（搜索 + 筛选 + 分页）。"""
    data = processed if processed is not None else load_processed()
    items = [_project(article_id, record) for article_id, record in _iter_records(data)]

    categories = sorted({item["category"] for item in items if item["category"]})

    needle = query.strip().lower()
    if needle:
        items = [item for item in items if needle in item["title"].lower()]
    if category:
        items = [item for item in items if item["category"] == category]
    if status:
        items = [item for item in items if item["status"] == status]

    # 新的在前；processed_at 为空的记录自然排到最后（空串最小）
    items.sort(key=lambda item: item["processed_at"] or "", reverse=True)

    total = len(items)
    safe_limit = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    safe_offset = max(0, int(offset or 0))
    return {
        "total": total,
        "limit": safe_limit,
        "offset": safe_offset,
        "categories": categories,
        "items": items[safe_offset : safe_offset + safe_limit],
    }


def get_article(
    article_id: str, *, processed: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """单篇文章详情。"""
    data = processed if processed is not None else load_processed()
    record = data.get(article_id)
    if not isinstance(record, dict) or not record.get("title"):
        return None
    item = _project(article_id, record)
    item["concepts"] = [str(c) for c in (record.get("concepts") or [])]
    item["tags"] = [str(t) for t in (record.get("tags") or [])]
    return item


def delete_article(article_id: str, config_manager: ConfigManager) -> list[str] | None:
    """级联删除文章及其关联数据。

    Returns:
        清理动作列表；文章不存在时返回 None。
    """
    processed = load_processed()
    if article_id not in processed:
        return None

    vault_path_str = str(config_manager.get("obsidian.vault_path", "") or "")
    if not vault_path_str:
        raise ValueError("未配置 obsidian.vault_path")
    articles_dir_name = str(
        config_manager.get("obsidian.articles_dir", "公众号文章") or "公众号文章"
    )

    return cascade_delete(
        Path(vault_path_str), Path(vault_path_str) / articles_dir_name, processed, article_id
    )


def open_in_obsidian(file_path: str) -> dict[str, Any]:
    """用 Obsidian 打开笔记（macOS `open obsidian://...`）。

    用 `path=` 绝对路径而不是 `vault=`+文件名：不依赖 vault 在 Obsidian 里的注册名。
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"笔记文件不存在: {path}")
    if sys.platform != "darwin":
        raise RuntimeError("仅支持 macOS")

    uri = f"obsidian://open?path={path}"
    result = subprocess.run(["open", uri], check=False, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "调用 open 失败")
    return {"ok": True, "uri": uri}
