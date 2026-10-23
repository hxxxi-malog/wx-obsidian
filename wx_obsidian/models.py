"""全局数据模型：CLI/Web/Orchestrator 共享的 dataclass 定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class ConnectionTestResult:
    """连通性测试结果。"""

    success: bool
    latency_ms: float
    message: str
    details: dict[str, str] | None = None


@dataclass
class LLMConfig:
    """LLM API 配置。"""

    provider: str
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7


@dataclass
class VisionConfig:
    """多模态 Vision API 配置。"""

    enabled: bool
    provider: str
    api_key: str
    base_url: str
    model: str
    max_concurrency: int = 10
    timeout: int = 120


@dataclass
class AccountStatus:
    """数据源账号状态（登录信息与续期标记）。

    `queried` 区分「查到了，没登录」和「压根查不到」：we-mp-rss 的登录态接口
    需要 Bearer Token，未配置时只能拿到 401。没有这个字段时两者都表现为
    `is_logged_in=False`，界面会把「查不到」显示成「未登录」，让人去排查一个
    根本不存在的问题。
    """

    is_logged_in: bool
    username: str | None = None
    expire_at: datetime | None = None
    need_refresh: bool = False
    queried: bool = True


@dataclass
class AccountProbe:
    """微信读书凭据探针结果：真的向上游要一次公众号信息。

    与 `AccountStatus.is_logged_in` 的区别：后者只说明「上游存了 Cookie」，
    这个探针说明「Cookie 还能用」。账号页据此决定要不要弹扫码登录。

    `state` 取值由数据源定义：valid / expired / token_invalid / unreachable / unknown。
    """

    state: str
    message: str = ""
    feed_name: str = ""
    checked_at: datetime | None = None


@dataclass
class ContainerStatus:
    """数据源服务/容器状态。"""

    is_running: bool
    container_id: str | None = None
    uptime: str | None = None
    health: str = "unknown"


@dataclass
class Feed:
    """数据源订阅的公众号。"""

    id: str
    name: str
    intro: str = ""
    cover: str = ""
    sync_time: datetime | None = None
    update_time: datetime | None = None


@dataclass
class SourceArticle:
    """数据源文章库的一篇文章（Web「最新文章」页浏览用）。

    与管线里的文章 dict 不同：这里只承载列表页需要的字段，不带正文。
    """

    article_id: str
    title: str
    url: str
    account: str
    account_id: str
    published_at: datetime | None = None
    cover: str = ""
    has_content: bool = False


@dataclass
class SourceArticlePage:
    """数据源文章库的分页结果（total 是上游总数，items 是当前页）。"""

    total: int
    offset: int
    limit: int
    items: list[SourceArticle] = field(default_factory=list)


@dataclass
class MigrationReport:
    """配置迁移报告。"""

    success: bool
    migrated_items: list[str] = field(default_factory=list)
    skipped_items: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    backup_path: Path | None = None


@dataclass
class FailedArticle:
    """失败文章记录。"""

    article_id: str
    title: str
    error: str
    failed_at: datetime = field(default_factory=datetime.now)
    retry_count: int = 0


@dataclass
class HealthStatus:
    """系统健康状态。"""

    source: ConnectionTestResult
    llm_api: ConnectionTestResult
    vision_api: ConnectionTestResult | None = None
    vault_path: ConnectionTestResult | None = None

    @property
    def overall(self) -> bool:
        """所有必须组件是否健康。"""
        return (
            self.source.success
            and self.llm_api.success
            and (not self.vision_api or self.vision_api.success)
            and (not self.vault_path or self.vault_path.success)
        )


@dataclass
class Statistics:
    """处理统计信息。"""

    total_articles: int = 0
    processed_articles: int = 0
    failed_articles: int = 0
    last_fetch_time: datetime | None = None
    categories: dict[str, int] = field(default_factory=dict)


@dataclass
class ProcessingResult:
    """单篇文章处理结果。"""

    article_id: str
    title: str
    status: str  # "done", "skipped", "error"
    category: str | None = None
    file_path: str | None = None
    error: str | None = None
