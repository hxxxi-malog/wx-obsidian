"""Web 交互层：FastAPI 本地服务 + Vue SPA。

与 `wx_obsidian/cli.py` 平级，只依赖 orchestrator / config_manager / models /
sources.factory。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    APP_VERSION = version("wx-obsidian")
except PackageNotFoundError:  # 冻结包可能没有 dist-info
    # 容器与源码直跑都不装包本体（见 Dockerfile 的 --no-install-project 思路），
    # 走的就是这个回退值 —— 发版时两处都要跟着 pyproject 的 version 改。
    APP_VERSION = "1.1.0"

APP_NAME = "wx-obsidian"

__all__ = ["APP_NAME", "APP_VERSION"]
