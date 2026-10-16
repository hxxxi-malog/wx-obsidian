"""运行日志缓冲：把 WARNING 以上的记录留一份在内存里，并推给正在运行的任务。

Web 下用户看不到终端输出（容器 / systemd / nohup 下都没有终端），所以排错信息必须在界面上能拿到。
只收集 WARNING 及以上，避免把管线里大量的 INFO 噪音灌进浏览器。
"""

from __future__ import annotations

import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_CAPACITY = 300


@dataclass
class LogEntry:
    """一条日志记录。"""

    seq: int
    ts: str
    level: str
    logger: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class LogBuffer(logging.Handler):
    """环形缓冲日志 handler，可挂到 root logger 上。"""

    def __init__(self, capacity: int = _CAPACITY, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._buffer: deque[LogEntry] = deque(maxlen=capacity)
        self._listeners: list[Callable[[LogEntry], None]] = []
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        self._seq += 1
        entry = LogEntry(
            seq=self._seq,
            ts=datetime.fromtimestamp(record.created, tz=UTC)
            .astimezone()
            .isoformat(timespec="seconds"),
            level=record.levelname,
            logger=record.name,
            message=record.getMessage(),
        )
        self._buffer.append(entry)
        for listener in list(self._listeners):
            # 监听方出错不能影响日志本身
            with contextlib.suppress(Exception):
                listener(entry)

    def subscribe(self, listener: Callable[[LogEntry], None]) -> None:
        """注册监听器（用于把日志推给正在运行的任务）。"""
        self._listeners.append(listener)

    def entries_since(self, since: int = 0) -> list[LogEntry]:
        """取 seq 大于 since 的记录。"""
        return [entry for entry in list(self._buffer) if entry.seq > since]

    @property
    def last_seq(self) -> int:
        return self._seq


_installed: LogBuffer | None = None


def install_log_buffer() -> LogBuffer:
    """挂上（或替换）全局日志缓冲，返回新的缓冲实例。"""
    global _installed
    if _installed is not None:
        logging.getLogger().removeHandler(_installed)
    buffer = LogBuffer()
    logging.getLogger().addHandler(buffer)
    _installed = buffer
    return buffer


def uninstall_log_buffer(buffer: LogBuffer) -> None:
    """摘掉日志缓冲（服务关闭时调用，避免测试间互相污染）。"""
    global _installed
    logging.getLogger().removeHandler(buffer)
    if _installed is buffer:
        _installed = None
