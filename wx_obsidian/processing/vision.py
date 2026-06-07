"""多模态 Vision API：图片语义描述、广告过滤、并发调用。"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

from wx_obsidian.processing.models import ImageDescription

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 预过滤：已知广告 URL 模式
# ---------------------------------------------------------------------------

_AD_URL_PATTERNS = re.compile(
    r"qr_code|follow|ad_|sponsor|promo|mmbiz_qrcode|mp\.weixin\.qq\.com/mmbiz_qrcode",
    re.IGNORECASE,
)

_VISION_PROMPT = (
    "文章上下文：{before}...{after}\n\n"
    "请分析这张图片，返回 JSON：\n"
    '{{"description": "描述", "is_content": true/false, '
    '"type": "diagram|photo|infographic|ad|icon|qrcode|watermark|banner"}}\n\n'
    "description 要求（后续 LLM 理解图片的唯一信息来源）：\n"
    "- 图片类型 + 整体内容（如'三层架构图'、'流程图'）\n"
    "- 关键元素（模块、节点、数据流向）\n"
    "- 在文章中说明的问题\n"
    "- 直接写内容，不要写'这张图展示了'\n\n"
    "is_content 判断规则（必须严格遵守，宁可误判为 false，不可误判为 true）：\n\n"
    "is_content=false 的情况（只要符合以下任一条即为 false）：\n"
    "1. 图片中包含二维码、小程序码、扫码引导文字\n"
    "2. 图片是文章顶部的 banner/头图/封面图（通常是标题或品牌宣传）\n"
    "3. 图片包含'关注'、'点赞'、'在看'、'阅读原文'等引导文字\n"
    "4. 图片是公众号 logo、头像、水印、平台标识\n"
    "5. 图片是广告、赞助商、推广内容\n"
    "6. 图片包含'AI智能问答'、'加入群'、'数据库'等平台推广文字\n"
    "7. 图片是文末的推荐阅读、相关文章列表\n"
    "8. 图片是装饰性分隔线、空白占位图\n"
    "9. 无法看出与文章正文内容的具体关联\n\n"
    "is_content=true 的情况（必须同时满足）：\n"
    "1. 图片是文章正文的有机组成部分\n"
    "2. 图片帮助读者理解文章内容（如架构图、数据图、流程图、代码截图、示意图）\n"
    "3. 图片中的信息无法用简单文字替代\n\n"
    "重要：如果图片中包含任何推广、引导关注、二维码相关文字，必须标记为 is_content=false"
)


# ---------------------------------------------------------------------------
# 预过滤
# ---------------------------------------------------------------------------


def _pre_filter_images(images: list[dict[str, str]]) -> list[dict[str, str]]:
    """过滤已知广告 URL 模式。

    URL 同时匹配内容和广告模式时保留（宁可多调用 API，不误删正文图）。
    """
    kept: list[dict[str, str]] = []
    for img in images:
        url = img.get("url", "")
        if not url:
            continue
        if _AD_URL_PATTERNS.search(url):
            logger.debug("预过滤跳过广告 URL: %s", url[:80])
            continue
        kept.append(img)
    return kept


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------


def _parse_vision_response(text: str, url: str) -> ImageDescription:
    """解析 vision API 返回的 JSON，缺失字段使用默认值。"""
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        # 尝试从 markdown 代码块中提取
        clean = re.sub(r"```json\s*", "", text)
        clean = re.sub(r"```\s*$", "", clean.strip())
        match = re.search(r"\{[\s\S]*\}", clean)
        if match:
            try:
                data = json.loads(match.group(), strict=False)
            except json.JSONDecodeError:
                logger.warning("Vision 响应 JSON 解析失败: %s", url[:80])
                return ImageDescription(
                    url=url, description="", is_content=False, type="unknown", status="api_error"
                )
        else:
            logger.warning("Vision 响应无 JSON: %s", url[:80])
            return ImageDescription(
                url=url, description="", is_content=False, type="unknown", status="api_error"
            )

    return ImageDescription(
        url=url,
        description=str(data.get("description", "")),
        is_content=bool(data.get("is_content", False)),
        type=str(data.get("type", "unknown")),
        status="ok",
    )


# ---------------------------------------------------------------------------
# 单图 API 调用（含重试）
# ---------------------------------------------------------------------------


def _describe_single(img: dict[str, str], vision_config: dict[str, Any]) -> ImageDescription:
    """调用 vision API 描述单张图片，失败重试 2 次（共 3 次尝试）。"""
    url = img["url"]
    api_key = vision_config["api_key"]
    base_url = vision_config["base_url"]
    model = vision_config["model"]
    timeout = vision_config.get("timeout", 120)
    max_retries = vision_config.get("max_retries", 2)

    before = img.get("before", "")[-200:]
    after = img.get("after", "")[:200]
    prompt_text = _VISION_PROMPT.format(before=before, after=after)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        }
    ]

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": model, "max_tokens": 1024, "messages": messages},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return _parse_vision_response(text, url)

        except (requests.RequestException, KeyError, IndexError, TypeError) as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(
                    "Vision API 调用失败，重试 %d/%d: %s — %s",
                    attempt + 1,
                    max_retries,
                    url[:80],
                    e,
                )
                time.sleep(1 * (attempt + 1))

    logger.warning("Vision API 调用最终失败: %s — %s", url[:80], last_error)
    return ImageDescription(
        url=url, description="", is_content=False, type="unknown", status="api_error"
    )


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def describe_images(
    images: list[dict[str, str]], vision_config: dict[str, Any]
) -> list[ImageDescription]:
    """并发调用多模态 API，返回每张图的描述。

    Args:
        images: extract_images_with_context() 的返回值。
        vision_config: vision 配置字典，包含 api_key, base_url, model 等。

    Returns:
        ImageDescription 列表，与过滤后的输入一一对应。
    """
    if not images:
        return []

    filtered = _pre_filter_images(images)
    if not filtered:
        logger.info("VisionStage: 所有图片被预过滤，跳过 API 调用")
        return []

    logger.info("VisionStage: %d 张图片待处理（预过滤后）", len(filtered))
    max_workers = vision_config.get("max_concurrency", 10)
    results: dict[str, ImageDescription] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(_describe_single, img, vision_config): img["url"] for img in filtered
        }
        for future in as_completed(future_to_url):
            img_url = future_to_url[future]
            try:
                desc = future.result()
                results[img_url] = desc
                if desc.status == "ok":
                    logger.debug(
                        "Vision 描述完成: %s — %s",
                        img_url[:60],
                        desc.description[:50],
                    )
            except Exception:
                logger.warning("Vision 线程异常: %s", img_url[:80], exc_info=True)
                results[img_url] = ImageDescription(
                    url=img_url,
                    description="",
                    is_content=False,
                    type="unknown",
                    status="api_error",
                )

    # 保持与输入顺序一致
    ordered = [results[img["url"]] for img in filtered if img["url"] in results]

    content_count = sum(1 for d in ordered if d.is_content and d.status == "ok")
    logger.info(
        "VisionStage 完成: %d 张描述, %d 张正文内容图",
        len(ordered),
        content_count,
    )
    return ordered
