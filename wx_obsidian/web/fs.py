"""文件系统辅助：给「选择 Vault 目录」提供目录树数据。

浏览器沙箱拿不到本地绝对路径（`webkitdirectory` 只给相对路径），
所以目录浏览必须由后端提供。本模块**只列目录项，不返回任何文件内容**，
把接口的暴露面压到最小。
"""

from __future__ import annotations

import os
from pathlib import Path

_MAX_ENTRIES = 500


def list_dir(raw_path: str) -> dict[str, object]:
    """列出一个目录的直接子项。

    Args:
        raw_path: 目标目录（支持 ~ 展开）。

    Returns:
        {path, parent, entries:[{name, is_dir}], truncated}

    Raises:
        ValueError: 未给路径或不是绝对路径。
        FileNotFoundError: 路径不存在。
        NotADirectoryError: 路径不是目录。
        PermissionError: 无权限读取。
    """
    if not raw_path.strip():
        raise ValueError("缺少 path 参数")

    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError(f"必须是绝对路径: {raw_path}")
    if not path.exists():
        raise FileNotFoundError(f"路径不存在: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"不是目录: {path}")

    entries: list[dict[str, object]] = []
    truncated = False
    try:
        with os.scandir(path) as scanner:
            for entry in scanner:
                if len(entries) >= _MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    is_dir = entry.is_dir()
                except OSError:
                    continue
                entries.append({"name": entry.name, "is_dir": is_dir})
    except PermissionError as exc:
        raise PermissionError(f"无权限读取: {path}") from exc

    # 目录在前，同类按名称排序（大小写不敏感）
    entries.sort(key=lambda item: (not item["is_dir"], str(item["name"]).lower()))

    return {
        "path": str(path),
        "parent": str(path.parent) if path.parent != path else "",
        "entries": entries,
        "truncated": truncated,
    }
