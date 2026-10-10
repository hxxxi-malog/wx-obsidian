"""数据源工厂：按配置选择具体实现，供 cli / tui 复用。

刻意接收**普通值**而不是 ConfigManager，避免 sources/ 反向依赖配置管理层；
调用方（cli.py / tui/app.py）负责从配置里取值后传入。

双源开关解析优先级：
    1. 环境变量 WX_SOURCE（临时覆盖，便于回退验证）
    2. 配置项 source.type
    3. 默认 we_mp_rss
"""

from __future__ import annotations

import logging
import os

from wx_obsidian.sources.base import ArticleSource

logger = logging.getLogger(__name__)

SOURCE_WE_MP_RSS = "we_mp_rss"
SOURCE_WEWE = "wewe"

DEFAULT_SOURCE_TYPE = SOURCE_WE_MP_RSS
DEFAULT_WE_MP_RSS_URL = "http://localhost:8001"
DEFAULT_WEWE_URL = "http://localhost:4000"

VALID_SOURCE_TYPES = (SOURCE_WE_MP_RSS, SOURCE_WEWE)


def resolve_source_type(configured: str = "") -> str:
    """解析最终生效的数据源类型（环境变量优先）。"""
    env_value = os.environ.get("WX_SOURCE", "").strip().lower()
    if env_value:
        if env_value not in VALID_SOURCE_TYPES:
            logger.warning(
                "WX_SOURCE=%s 不是合法取值 %s，将回退到配置值", env_value, VALID_SOURCE_TYPES
            )
        else:
            return env_value

    candidate = (configured or "").strip().lower()
    if candidate in VALID_SOURCE_TYPES:
        return candidate
    if candidate:
        logger.warning("source.type=%s 不是合法取值，使用默认 %s", candidate, DEFAULT_SOURCE_TYPE)
    return DEFAULT_SOURCE_TYPE


def create_article_source(
    source_type: str,
    base_url: str = "",
    timeout: int = 15,
    token: str = "",
    wewe_auth_code: str = "",
) -> ArticleSource:
    """构造文章数据源实例。

    Args:
        source_type: `we_mp_rss` 或 `wewe`（建议先用 resolve_source_type() 归一化）。
        base_url: 数据源服务地址；留空时用该类型的默认地址。
        timeout: 请求超时（秒）。
        token: we-mp-rss 管理接口的 Bearer Token（仅订阅管理等接口需要）。
        wewe_auth_code: 旧 WeWe RSS 的 AUTH_CODE。

    Returns:
        实现了 ArticleSource 协议的客户端实例。
    """
    if source_type == SOURCE_WEWE:
        # 延迟导入：旧源保留仅为回退，避免其在未来被删除时影响新源导入
        from wx_obsidian.wewe_rss import WeWeRSSClient

        return WeWeRSSClient(base_url or DEFAULT_WEWE_URL, wewe_auth_code)

    from wx_obsidian.sources.we_mp_rss import WeMpRssClient

    return WeMpRssClient(base_url or DEFAULT_WE_MP_RSS_URL, timeout=timeout, token=token)
