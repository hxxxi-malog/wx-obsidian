"""数据源工厂：按配置选择具体实现，供 cli / web 复用。

刻意接收**普通值**而不是 ConfigManager，避免 sources/ 反向依赖配置管理层；
调用方（cli.py / web/runtime.py）负责从配置里取值后传入。

数据源开关解析优先级：
    1. 环境变量 WX_SOURCE（临时覆盖，便于排查）
    2. 配置项 source.type
    3. 默认 we_mp_rss

当前只支持 we-mp-rss：旧的 WeWe RSS 已归档、其闭源中转下线，
相关实现与配置已在本次迁移中移除（背景见
docs/design-docs/wewe-rss-replacement/spec.md）。
"""

from __future__ import annotations

import logging
import os

from wx_obsidian.sources.base import ArticleSource

logger = logging.getLogger(__name__)

SOURCE_WE_MP_RSS = "we_mp_rss"

DEFAULT_SOURCE_TYPE = SOURCE_WE_MP_RSS
DEFAULT_WE_MP_RSS_URL = "http://localhost:8001"

VALID_SOURCE_TYPES = (SOURCE_WE_MP_RSS,)


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
) -> ArticleSource:
    """构造文章数据源实例。

    Args:
        source_type: `we_mp_rss`（建议先用 resolve_source_type() 归一化）。
        base_url: 数据源服务地址；留空时用该类型的默认地址。
        timeout: 请求超时（秒）。
        token: we-mp-rss 管理接口的 Bearer Token（订阅列表、账号状态等需凭据的接口用）。

    Returns:
        实现了 ArticleSource 协议的客户端实例。
    """
    from wx_obsidian.sources.we_mp_rss import WeMpRssClient

    return WeMpRssClient(base_url or DEFAULT_WE_MP_RSS_URL, timeout=timeout, token=token)
