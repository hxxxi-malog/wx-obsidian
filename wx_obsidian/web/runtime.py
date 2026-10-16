"""Web 运行期装配：ConfigManager + 数据源 + Orchestrator。

装配步骤与 `cli.py` 保持一致（ConfigManager → create_article_source → Orchestrator），
集中在这里是为了让 Web 层与 CLI 用同一套取值与数据源地址解析。
"""

from __future__ import annotations

from dataclasses import dataclass

from wx_obsidian.config import resolve_source_base_url
from wx_obsidian.config_manager import ConfigManager
from wx_obsidian.orchestrator import Orchestrator
from wx_obsidian.sources.base import ArticleSource
from wx_obsidian.sources.factory import create_article_source, resolve_source_type


@dataclass
class WebRuntime:
    """Web 层运行期依赖集合。"""

    config_manager: ConfigManager
    source: ArticleSource
    orchestrator: Orchestrator


def build_runtime(config_manager: ConfigManager | None = None) -> WebRuntime:
    """构造 Web 运行期。

    Args:
        config_manager: 可注入的配置管理器（测试用）；默认新建并 load()。

    Returns:
        装配完成的 WebRuntime。
    """
    cm = config_manager or ConfigManager()
    cm.load()

    # 容器里数据源地址是服务名、宿主是 localhost，而 config.json 只有一份
    # （processed.json 必须共用），故统一走这个解析（见 config.resolve_source_base_url）
    base_url = resolve_source_base_url(str(cm.get("source.base_url", "") or ""))

    source = create_article_source(
        resolve_source_type(cm.get("source.type", "")),
        base_url=base_url,
        timeout=int(cm.get("source.timeout", 15) or 15),
        token=str(cm.get("source.token", "") or ""),
    )

    return WebRuntime(
        config_manager=cm,
        source=source,
        orchestrator=Orchestrator(cm, source),
    )
