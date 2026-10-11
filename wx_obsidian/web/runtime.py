"""Web 运行期装配：ConfigManager + 数据源 + Orchestrator + 调度器。

装配逻辑与 `tui/app.py:__init__` 一致。刻意复刻而非抽取共享函数：
P0 不动已跑通的 TUI，避免把 Web 的回归风险带进 TUI。
"""

from __future__ import annotations

from dataclasses import dataclass

from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.orchestrator import Orchestrator
from wx_obsidian.scheduler import TaskScheduler
from wx_obsidian.sources.base import ArticleSource
from wx_obsidian.sources.factory import create_article_source, resolve_source_type


@dataclass
class WebRuntime:
    """Web 层运行期依赖集合。"""

    config_manager: ConfigManager
    source: ArticleSource
    orchestrator: Orchestrator
    scheduler: TaskScheduler


def build_runtime(config_manager: ConfigManager | None = None) -> WebRuntime:
    """构造 Web 运行期。

    Args:
        config_manager: 可注入的配置管理器（测试用）；默认新建并 load()。

    Returns:
        装配完成的 WebRuntime。
    """
    cm = config_manager or ConfigManager()
    cm.load()

    source = create_article_source(
        resolve_source_type(cm.get("source.type", "")),
        base_url=str(cm.get("source.base_url", "") or ""),
        timeout=int(cm.get("source.timeout", 15) or 15),
        token=str(cm.get("source.token", "") or ""),
    )

    return WebRuntime(
        config_manager=cm,
        source=source,
        orchestrator=Orchestrator(cm, source),
        scheduler=TaskScheduler(),
    )
