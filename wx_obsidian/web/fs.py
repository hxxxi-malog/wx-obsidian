"""文件系统辅助：给「选择 Vault 目录」提供目录树数据 + 原生面板。

浏览器沙箱拿不到本地绝对路径（`webkitdirectory` 只给相对路径），
所以目录浏览必须由后端提供。`list_dir` **只列目录项，不返回任何文件内容**，
把接口的暴露面压到最小；`pick_directory` 则借 macOS 原生面板拿到绝对路径，
并顺带为该 app 建立 TCC 访问许可（用户在系统面板里主动选中的目录）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_MAX_ENTRIES = 500
# 面板是模态的，用户可能思考一会儿；给足时间但不无限等
_PICK_TIMEOUT_SECONDS = 300.0


class PickerError(RuntimeError):
    """原生目录选择器不可用或执行失败。"""


class PickerUnavailableError(PickerError):
    """当前平台没有原生目录选择器（非 macOS，或缺 osascript）。"""


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


def _applescript_string(text: str) -> str:
    """把文本转成 AppleScript 字符串字面量（反斜杠与双引号转义）。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def pick_directory(prompt: str = "选择 Obsidian Vault") -> str:
    """弹 macOS 原生「选择文件夹」面板。

    **这同时是解决 TCC 的正确姿势**：用户在系统面板里主动选择的目录，
    会为该 app（而非终端）建立访问许可，比事后去「系统设置 → 隐私」里
    手工授权可靠得多。

    Args:
        prompt: 面板上的提示文案。

    Returns:
        选中的绝对路径；用户取消时返回空串（取消不是错误）。

    Raises:
        PickerUnavailableError: 非 macOS 环境，或找不到 osascript。
        PickerError: osascript 执行失败或超时。
    """
    if sys.platform != "darwin":
        raise PickerUnavailableError(f"原生目录选择器仅支持 macOS（当前平台 {sys.platform}）")
    if shutil.which("osascript") is None:
        raise PickerUnavailableError("找不到 osascript，请改用目录浏览手动输入路径")

    script = f"POSIX path of (choose folder with prompt {_applescript_string(prompt)})"
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=_PICK_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PickerError(f"目录选择器超时（{_PICK_TIMEOUT_SECONDS:.0f} 秒）") from exc
    except OSError as exc:
        raise PickerError(f"无法执行 osascript: {exc}") from exc

    if result.returncode != 0:
        # 取消有两种常见文案：User canceled / 用户已取消
        stderr = (result.stderr or "").strip()
        if "cancel" in stderr.lower() or "-128" in stderr:
            return ""
        raise PickerError(stderr or f"osascript 退出码 {result.returncode}")

    # POSIX path of (choose folder) 结尾带 "/"，Path 归一化会去掉
    chosen = (result.stdout or "").strip()
    return str(Path(chosen)) if chosen else ""
