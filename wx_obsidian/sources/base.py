"""数据源抽象：编排层只依赖此 Protocol，不绑定具体实现。

迁移背景见 docs/design-docs/wewe-rss-replacement/spec.md：
WeWe RSS 已归档且其闭源中转下线，改用 we-mp-rss 作为文章源。
为了让「双源开关」可回退，编排层（orchestrator / cli / tui）统一面向
`ArticleSource` 编程，具体实现由 `create_article_source()` 按配置选择。

依赖隔离：本模块只依赖 models，不依赖 processing/ output/。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from wx_obsidian.models import AccountStatus, Feed


@runtime_checkable
class ArticleSource(Protocol):
    """文章数据源需要提供的能力。

    实现方：`WeMpRssClient`（we-mp-rss，当前默认）、`WeWeRSSClient`（旧源，回退用）。
    """

    def get_articles(self, limit: int = 0) -> list[dict[str, Any]]:
        """拉取文章列表。

        Returns:
            统一契约的文章 dict 列表，字段：
            `id` / `title` / `url` / `content` / `date_published` /
            `author` / `_account_name`。`id` 必须是
            `mp.weixin.qq.com/s/<token>` 的 token，以兼容 processed.json。
        """
        ...

    def is_healthy(self) -> bool:
        """数据源服务是否可达。"""
        ...

    def get_account_status(self) -> AccountStatus:
        """账号登录态（含是否需要刷新）。"""
        ...

    def get_feeds(self) -> list[Feed]:
        """已订阅的公众号列表。"""
        ...

    def add_feed(self, article_url: str) -> Feed | None:
        """按文章链接添加公众号。返回 None 表示该数据源不支持此方式。"""
        ...

    def delete_feed(self, feed_id: str) -> bool:
        """按 feed id 取消订阅。"""
        ...

    def refresh_cookie(self) -> bool:
        """刷新登录态。"""
        ...

    def get_login_url(self) -> str:
        """引导用户完成登录/授权的页面地址。"""
        ...
