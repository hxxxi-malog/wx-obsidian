"""数据源抽象：编排层只依赖此 Protocol，不绑定具体实现。

迁移背景见 docs/design-docs/wewe-rss-replacement/spec.md：
WeWe RSS 已归档且其闭源中转下线，改用 we-mp-rss 作为文章源。
编排层（orchestrator / cli / web）统一面向 `ArticleSource` 编程，
具体实现由 `create_article_source()` 按配置选择。

依赖隔离：本模块只依赖 models，不依赖 processing/ output/。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from wx_obsidian.models import AccountProbe, AccountStatus, Feed, SourceArticlePage


class SourceAuthError(Exception):
    """数据源管理接口的访问令牌缺失或已失效（上游返回 401/403）。

    Web 层据此提示用户去配置访问令牌；把它混进普通网络故障会让人查错方向。
    """


@runtime_checkable
class ArticleSource(Protocol):
    """文章数据源需要提供的能力。

    实现方：`WeMpRssClient`（we-mp-rss）。
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

    def list_articles(
        self,
        *,
        offset: int = 0,
        limit: int = 30,
        feed_id: str = "",
    ) -> SourceArticlePage:
        """读取数据源文章库（按发布时间倒序，分页）。

        Args:
            offset: 起始偏移。
            limit: 单页条数（上游上限 100）。
            feed_id: 只取单个公众号；空串表示全部订阅。

        Raises:
            SourceAuthError: 访问令牌缺失或失效。
        """
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

    def probe_account(self) -> AccountProbe:
        """真实校验一次登录凭据是否还有效（不是「有没有存凭据」）。"""
        ...

    def get_login_qr(self) -> dict[str, Any]:
        """取扫码登录二维码，返回 `{"image": "data:image/png;base64,...", "uid": str}`。"""
        ...

    def get_login_qr_status(self) -> dict[str, Any]:
        """二维码/扫码状态，返回 `{"login_status", "message", "logic_code"}`。"""
        ...

    def finish_login_qr(self) -> dict[str, Any]:
        """扫码完成后收尾，返回 `{"login_status", "message"}`。"""
        ...

    def refresh_feed(self, feed_id: str) -> str:
        """触发单个公众号采集，返回 triggered / skipped / failed。"""
        ...
