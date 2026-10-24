"""文件系统辅助：给「选择 Vault 目录」提供目录树数据 + 原生面板。

浏览器沙箱拿不到本地绝对路径（`webkitdirectory` 只给相对路径），
所以目录浏览必须由后端提供。`list_dir` **只列目录项，不返回任何文件内容**，
把接口的暴露面压到最小；`pick_directory` 则借 macOS 原生面板拿到绝对路径。

面板交给 System Events 承载：裸 `choose folder` 由 osascript 自己承载，而服务进程
没有窗口，面板会开在浏览器后面（参考实现实测，macOS 26；带不带 `activate` 都一样）。
System Events 承载时面板在前台；跨进程回复默认 2 分钟就放弃，所以脚本里必须写
`with timeout`。用户取消时 osascript 退出码 1、stderr 带 `(-128)`，这是正常结果。
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_MAX_ENTRIES = 500
# 面板是模态的，用户可能思考一会儿；给足时间但不无限等
_PICK_TIMEOUT_SECONDS = 300.0
# 跨进程回复默认 2 分钟放弃，脚本内显式放宽到一天（真正的上限在 Python 侧）
_APPLESCRIPT_TIMEOUT_SECONDS = 86400
_CLOSE_DIALOG_SCRIPT = 'quit app "System Events"'
_DEFAULT_PROMPT = "选择 Obsidian Vault"

# 同一时间只允许一个面板（System Events 承载的对话框叠不了第二个）
_pick_lock = threading.Lock()
_state_lock = threading.Lock()
_active_process: subprocess.Popen[str] | None = None
_cancel_requested = False


class PickerError(RuntimeError):
    """原生目录选择器不可用或执行失败。"""


class PickerUnavailableError(PickerError):
    """当前平台没有原生目录选择器（非 macOS，或缺 osascript），或没有自动化授权。"""


class PickerBusyError(PickerError):
    """已经有一个目录选择面板打开。"""


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


def native_platform() -> bool:
    """当前进程是否与 macOS 图形会话处于同一平台。"""
    return sys.platform == "darwin"


def picker_available() -> bool:
    """当前进程能否直接弹出原生目录面板。"""
    return native_platform() and shutil.which("osascript") is not None


def _applescript_string(text: str) -> str:
    """把文本转成 AppleScript 字符串字面量（反斜杠与双引号转义）。"""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _normalize_prompt(prompt: str) -> str:
    """提示文案只保留单行：换行进 AppleScript 字面量会破坏语法。"""
    text = " ".join(str(prompt).split())
    return (text or _DEFAULT_PROMPT)[:120]


def pick_script(prompt: str = _DEFAULT_PROMPT) -> list[str]:
    """构造 osascript 参数：System Events 承载面板并带回绝对路径。"""
    literal = _applescript_string(_normalize_prompt(prompt))
    return [
        "osascript",
        "-e",
        f"with timeout of {_APPLESCRIPT_TIMEOUT_SECONDS} seconds",
        "-e",
        'tell application "System Events"',
        "-e",
        "activate",
        "-e",
        f"POSIX path of (choose folder with prompt {literal})",
        "-e",
        "end tell",
        "-e",
        "end timeout",
    ]


def _parse_pick_output(returncode: int, stdout: str, stderr: str) -> str:
    """把 osascript 的退出码与输出解释成路径：取消为空串，失败抛异常。"""
    text = (stderr or "").strip()
    if returncode != 0:
        lowered = text.lower()
        if "cancel" in lowered or "-128" in text:
            return ""
        if "-1743" in text or "not authorized" in lowered:
            raise PickerUnavailableError(
                "没有控制 System Events 的权限：请在「系统设置 → 隐私与安全性 → 自动化」里"
                f"允许当前进程控制 System Events（原文：{text}）"
            )
        raise PickerError(text or f"osascript 退出码 {returncode}")

    chosen = (stdout or "").strip()
    if not chosen:
        return ""
    # POSIX path of (choose folder) 结尾带 "/"，Path 归一化会去掉（根目录除外）
    return str(Path(chosen))


def _close_dialog() -> None:
    """关掉 System Events 承载的面板：终止 osascript 关不掉这个对话框。"""
    if shutil.which("osascript") is None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["osascript", "-e", _CLOSE_DIALOG_SCRIPT],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )


def cancel_pick() -> bool:
    """取消正在打开的面板；没有面板时返回 False。"""
    global _cancel_requested
    with _state_lock:
        process = _active_process
        if process is None:
            return False
        _cancel_requested = True
    process.terminate()
    _close_dialog()
    return True


def pick_directory(prompt: str = _DEFAULT_PROMPT) -> str:
    """弹 macOS 原生「选择文件夹」面板。

    Args:
        prompt: 面板上的提示文案（单行，超长会截断）。

    Returns:
        选中的绝对路径；用户取消时返回空串（取消不是错误）。

    Raises:
        PickerUnavailableError: 非 macOS 环境、找不到 osascript，或没有自动化授权。
        PickerBusyError: 已经有一个面板打开。
        PickerError: osascript 执行失败或超时。
    """
    global _active_process, _cancel_requested
    if not native_platform():
        raise PickerUnavailableError(f"原生目录选择器仅支持 macOS（当前平台 {sys.platform}）")
    if shutil.which("osascript") is None:
        raise PickerUnavailableError("找不到 osascript，请改用目录浏览手动输入路径")
    if not _pick_lock.acquire(blocking=False):
        raise PickerBusyError("已经有一个目录选择面板打开，请先处理它")

    try:
        with _state_lock:
            _cancel_requested = False
        try:
            process = subprocess.Popen(
                pick_script(prompt),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise PickerError(f"无法执行 osascript: {exc}") from exc
        with _state_lock:
            _active_process = process

        try:
            try:
                stdout, stderr = process.communicate(timeout=_PICK_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                process.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.communicate(timeout=5)
                _close_dialog()
                raise PickerError(f"目录选择器超时（{_PICK_TIMEOUT_SECONDS:.0f} 秒）") from exc
        finally:
            with _state_lock:
                _active_process = None
                cancelled = _cancel_requested

        if cancelled:
            return ""
        returncode = process.returncode
        if returncode is None:
            raise PickerError("osascript 没有返回退出码")
        return _parse_pick_output(returncode, stdout, stderr)
    finally:
        _pick_lock.release()
