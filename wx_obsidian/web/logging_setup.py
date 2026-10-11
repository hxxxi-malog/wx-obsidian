"""日志落盘：`--windowed` 的 .app 里唯一的排错通道。

**为什么必须有这个模块**：`.app` 以 `console=False` 打包（`packaging/wx-obsidian.spec`），
双击启动时没有终端。一旦启动阶段就失败（端口被占、配置损坏、资源缺失），
用户看到的现象是「双击了，什么都没发生」——没有任何可自查的线索。
所以日志必须落盘；而且 **stdout/stderr 也要一起落盘**：管线的进度是 `print()`
出来的，不接管这一步，日志里就只剩 uvicorn 的启动记录，看不到真正在做的事。

两条设计取舍：

- **轮转按大小不按天**：崩溃循环下按天轮转会让当天文件无限增长；
  按大小（2MB × 3）给出确定的上限。
- **写日志失败不能让启动失败**：日志目录不可写时降级为「只打终端」，
  绝不因为排错设施本身把应用挡在门外。
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

LOG_DIR_NAME = "wx-obsidian"
LOG_FILE_NAME = "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

MASK = "****"

# 三类会进日志的敏感值：模型 Key、URL 里的 token、Authorization 头
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{6,}"),
    re.compile(r"(?i)(token=)[^&\s\"']+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9_.-]{6,}"),
)


def mask_secrets(text: str) -> str:
    """把文本里的密钥/token 掩码掉。

    Args:
        text: 原始文本。

    Returns:
        掩码后的文本；无敏感内容时原样返回。
    """
    masked = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            masked = pattern.sub(lambda match: match.group(1) + MASK, masked)
        else:
            masked = pattern.sub("sk-" + MASK, masked)
    return masked


class MaskSecretsFilter(logging.Filter):
    """在日志记录落到 handler 之前把密钥掩码掉。

    直接改写 `record.msg` 并清空 `args`（`getMessage()` 已预先格式化），
    这样任何 handler——包括 Web 界面用的 `LogBuffer`——都拿不到未掩码的文本。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):  # 参数不匹配时不要吞掉这条日志
            return True
        masked = mask_secrets(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


class _LogStream(io.TextIOBase):
    """把 ``print()`` 的输出转发到日志。

    只接管 Python 层的 `sys.stdout` / `sys.stderr`；C 扩展直接写 fd 的输出
    不在范围内（uvicorn 自身走 logging，已在范围内）。
    """

    def __init__(self, logger: logging.Logger, level: int, fallback: TextIO | None) -> None:
        super().__init__()
        self._logger = logger
        self._level = level
        self._fallback = fallback
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, "%s", line)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, "%s", self._buffer)
        self._buffer = ""
        if self._fallback is not None:
            with contextlib.suppress(OSError, ValueError):
                self._fallback.flush()


def quiet_noisy_libraries() -> None:
    """压掉第三方库的启动噪音。

    必须在这些库 **import 之后** 才生效：jieba 在 import 时把自己的 logger
    设回 `DEBUG`（`jieba/__init__.py:29`），先前的 `setLevel` 会被它覆盖，
    所以这里先 import 再压级别。它每次加载词典都会往 stderr 打三行 INFO，
    而冻结包把 stderr 重定向成 ERROR，日志里就会出现一排假的 `[ERROR]`——
    真错误正好被淹没。
    """
    try:
        import jieba  # noqa: F401 - 先 import 才能压住它 import 时设的 DEBUG
    except ImportError:  # 没装 jieba 时无需处理
        return
    logging.getLogger("jieba").setLevel(logging.WARNING)


def default_log_file() -> Path:
    """默认日志路径：macOS 走 `~/Library/Logs/`，其它平台退回配置目录。"""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / LOG_DIR_NAME / LOG_FILE_NAME
    return Path.home() / ".wx-obsidian" / "logs" / LOG_FILE_NAME


def resolve_level(raw: str, default: int = logging.INFO) -> int:
    """把配置里的字符串级别转成 logging 级别（非法值退回默认）。"""
    level = logging.getLevelName(str(raw or "").strip().upper())
    return level if isinstance(level, int) else default


def configure_logging(
    *,
    level: str = "INFO",
    log_file: Path | None = None,
    redirect_streams: bool = False,
) -> Path | None:
    """装配控制台 + 轮转文件日志。

    Args:
        level: 日志级别字符串。
        log_file: 覆盖默认日志路径（测试用）。
        redirect_streams: 是否把 `print()` 输出一并写进日志文件。冻结包
            （无终端）传 True；源码运行保持终端原样，便于开发。

    Returns:
        实际使用的日志文件路径；无法落盘时返回 None（调用方据此提示用户）。
    """
    resolved = resolve_level(level)
    formatter = logging.Formatter(LOG_FORMAT)
    masker = MaskSecretsFilter()

    root = logging.getLogger()
    root.setLevel(resolved)

    if sys.stderr is not None:  # 冻结的 windowed 形态下可能为 None
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    path: Path | None = log_file or default_log_file()
    try:
        assert path is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "无法写入日志文件 %s，仅输出到终端", path, exc_info=True
        )
        path = None
    else:
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    for handler in root.handlers:
        handler.addFilter(masker)

    if redirect_streams and path is not None:
        # 刻意用 Any 承接：这是对 sys.stdout/sys.stderr 的鸭子替换，
        # 按 AGENTS.md 红线 1 不能用 type: ignore，故走 Any 声明。
        stdout_stream: Any = _LogStream(
            logging.getLogger("wx_obsidian.stdout"), logging.INFO, sys.stdout
        )
        stderr_stream: Any = _LogStream(
            logging.getLogger("wx_obsidian.stderr"), logging.ERROR, sys.stderr
        )
        sys.stdout = stdout_stream
        sys.stderr = stderr_stream

    return path
