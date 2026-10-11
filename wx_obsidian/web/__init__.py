"""Web 交互层：FastAPI 本地服务 + Vue SPA。

与 `wx_obsidian/tui/`、`wx_obsidian/cli.py` 平级，只依赖 orchestrator /
config_manager / models / sources.factory；**不得** import `wx_obsidian.tui.*`。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("wx-obsidian")
except PackageNotFoundError:  # 冻结包可能没有 dist-info
    APP_VERSION = "0.1.0"

APP_NAME = "wx-obsidian"

__all__ = ["APP_NAME", "APP_VERSION"]
