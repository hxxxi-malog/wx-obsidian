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
