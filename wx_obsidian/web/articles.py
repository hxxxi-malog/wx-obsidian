"""文章列表投影：从 processed.json 读文章，复用既有的级联删除。

刻意不改 `output/cleanup.py`：Web 只是给 `cascade_delete` 换个调用方。
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

from wx_obsidian.config import load_processed
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.output.cleanup import cascade_delete

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 500

# N1 排序白名单：只认这三个字段与两个方向，其余值一律回落到默认顺序
_SORT_FIELDS = ("processed_at", "title", "category")
_SORT_ORDERS = ("asc", "desc")

# Obsidian 的仓库注册表（macOS）；只读，用于判断笔记能否被打开
_OBSIDIAN_REGISTRY = Path("~/Library/Application Support/obsidian/obsidian.json")


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


def _sort_items(items: list[dict[str, Any]], sort: str, order: str) -> None:
    """就地排序（必须在分页切片**之前**调用，否则每页各自排序会跨页重复/遗漏）。

    `sort` / `order` 任一缺失或非法 → 完全回落到改动前的默认顺序（`processed_at`
    降序、稳定排序），老请求逐条不变；合法时缺字段的项一律排最后，排序键相等时用
    `article_id` 做稳定次级键，保证同一批数据按页切分不重不漏。
    """
    if sort not in _SORT_FIELDS or order not in _SORT_ORDERS:
        # 老的默认：新的在前；processed_at 为空的记录自然排到最后（空串最小）
        items.sort(key=lambda item: item["processed_at"] or "", reverse=True)
        return

    descending = order == "desc"

    def key(item: dict[str, Any]) -> tuple[bool, str, str]:
        value = str(item.get(sort) or "")
        missing = value == ""
        # 降序时整个元组被 reverse 翻转，故用「有值」当第一段，缺字段的才会落到最后
        return (not missing if descending else missing, value, str(item["article_id"]))

    items.sort(key=key, reverse=descending)


def list_articles(
    *,
    query: str = "",
    category: str = "",
    status: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    sort: str = "",
    order: str = "",
    processed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """文章列表（搜索 + 筛选 + 排序 + 分页）。

    Args:
        sort: 排序字段白名单 `processed_at` / `title` / `category`；其余值走默认顺序。
        order: `asc` / `desc`；其余值走默认顺序。
    """
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

    # 排序在分页切片之前；参数非法时这里就是改动前的默认顺序
    _sort_items(items, sort, order)

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


class VaultNotRegisteredError(RuntimeError):
    """笔记不在任何已注册的 Obsidian 仓库内。

    Obsidian 的 `obsidian://open?path=` 只能打开**已注册仓库**里的文件，
    否则它自己弹一个「Vault not found」对话框——用户根本不知道要做什么。
    这个异常负责把「该做什么」说清楚。
    """


def load_obsidian_vaults(registry_path: Path | None = None) -> list[Path] | None:
    """读取 Obsidian 已注册的仓库路径。

    Args:
        registry_path: 注册表位置；省略时用 macOS 默认位置。

    Returns:
        仓库路径列表；注册表不存在或不可读时返回 None（表示无法判断，调用方应放行）。
    """
    path = (registry_path or _OBSIDIAN_REGISTRY).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vaults = data.get("vaults")
    if not isinstance(vaults, dict):
        return None

    result: list[Path] = []
    for info in vaults.values():
        if isinstance(info, dict):
            raw = str(info.get("path") or "").strip()
            if raw:
                result.append(Path(raw))
    return result


def find_containing_vault(file_path: Path, vaults: list[Path]) -> Path | None:
    """找出包含该笔记的仓库；嵌套仓库时取路径最长的那个。"""
    try:
        target = file_path.resolve()
    except OSError:
        target = file_path
    best: Path | None = None
    for vault in vaults:
        try:
            resolved = vault.resolve()
        except OSError:
            continue
        if (target == resolved or resolved in target.parents) and (
            best is None or len(str(resolved)) > len(str(best))
        ):
            best = resolved
    return best


def suggest_vault_root(file_path: Path) -> Path | None:
    """向上找带 `.obsidian` 的目录——那才是 Obsidian 认的仓库根。"""
    for parent in file_path.parents:
        if (parent / ".obsidian").is_dir():
            return parent
    return None


def _vault_required_message(path: Path, vaults: list[Path]) -> str:
    lines = [
        "这篇笔记所在的目录还没有在 Obsidian 里注册为仓库，所以打不开。",
        f"笔记路径：{path}",
    ]
    root = suggest_vault_root(path)
    if root is not None:
        lines.append(f"请在 Obsidian 里用「打开文件夹作为仓库」，选择：{root}")
    else:
        lines.append(f"笔记所在目录：{path.parent}（其中没有 .obsidian，可能已不是仓库根）")
    if vaults:
        lines.append("当前已注册的仓库：" + "、".join(str(vault) for vault in vaults[:3]))
    return "\n".join(lines)


def open_in_obsidian(file_path: str, *, registry_path: Path | None = None) -> dict[str, Any]:
    """用 Obsidian 打开笔记（macOS `open obsidian://...`）。

    用 `path=` 绝对路径而不是 `vault=`+文件名：绝对路径无歧义，也不依赖仓库名唯一。
    但 Obsidian 只认**已注册仓库**里的文件，故先查注册表；查不到就给出可执行的提示，
    而不是让 Obsidian 弹一个「Vault not found」。

    Args:
        file_path: 笔记的绝对路径。
        registry_path: Obsidian 注册表位置（测试用）。

    Returns:
        {"ok": True, "uri": ..., "vault": ...}

    Raises:
        FileNotFoundError: 笔记文件不存在。
        VaultNotRegisteredError: 笔记不在任何已注册仓库内。
        RuntimeError: 非 macOS，或调用 `open` 失败。
    """
    path = Path(file_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"笔记文件不存在: {path}")
    if sys.platform != "darwin":
        raise RuntimeError("仅支持 macOS")

    vaults = load_obsidian_vaults(registry_path)
    containing: Path | None = None
    if vaults is not None:
        containing = find_containing_vault(path, vaults)
        if containing is None:
            raise VaultNotRegisteredError(_vault_required_message(path, vaults))

    # 自己百分号编码：路径里若含 `#`/`?`/`&`，不编码会被当成 URI 的片段或参数而截断
    uri = f"obsidian://open?path={quote(str(path), safe='/')}"
    result = subprocess.run(["open", uri], check=False, capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "调用 open 失败")
    return {"ok": True, "uri": uri, "vault": containing.name if containing else ""}
