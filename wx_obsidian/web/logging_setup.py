"""日志落盘 + 密钥掩码：Web 服务的排错通道。

**为什么必须有这个模块**：Web 服务常以「没有终端」的方式跑——容器
（`docker compose`）、systemd、nohup 都是。一旦启动阶段就失败（端口被占、
配置损坏、资源缺失），终端里看不到任何线索，只能靠日志回溯。

两条设计取舍：

- **轮转按大小不按天**：崩溃循环下按天轮转会让当天文件无限增长；
  按大小（2MB × 3）给出确定的上限。
- **写日志失败不能让启动失败**：日志目录不可写时降级为「只打终端」，
  绝不因为排错设施本身把应用挡在门外。
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


def quiet_noisy_libraries() -> None:
    """压掉第三方库的启动噪音。

    必须在这些库 **import 之后** 才生效：jieba 在 import 时把自己的 logger
    设回 `DEBUG`（`jieba/__init__.py:29`），先前的 `setLevel` 会被它覆盖，
    所以这里先 import 再压级别。它每次加载词典都会往 stderr 打三行 INFO，
    混进日志里就是三行纯噪音。
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
) -> Path | None:
    """装配控制台 + 轮转文件日志。

    Args:
        level: 日志级别字符串。
        log_file: 覆盖默认日志路径（测试用）。

    Returns:
        实际使用的日志文件路径；无法落盘时返回 None（调用方据此提示用户）。
    """
    resolved = resolve_level(level)
    formatter = logging.Formatter(LOG_FORMAT)
    masker = MaskSecretsFilter()

    root = logging.getLogger()
    root.setLevel(resolved)

    if sys.stderr is not None:  # 脱离终端运行时 stderr 可能为 None（如 pythonw）
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

    return path
