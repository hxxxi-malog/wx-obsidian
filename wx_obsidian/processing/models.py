"""数据结构：Pipeline 上下文、图片描述。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ImageDescription:
    """单张图片的语义描述。"""

    url: str
    description: str
    is_content: bool  # True=正文内容图，False=广告/装饰图
    type: str  # diagram/photo/infographic/ad/icon/qrcode
    status: str  # ok/filtered/api_error


@dataclass
class PipelineContext:
    """Pipeline 各 stage 间传递的上下文。"""

    article: dict[str, Any]
    content: str
    images: list[dict[str, str]]
    image_descriptions: list[ImageDescription] | None = None
    summary_data: dict[str, Any] | None = None
    md_content: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    processed: dict[str, Any] = field(default_factory=dict)
