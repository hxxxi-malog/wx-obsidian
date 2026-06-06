"""LLM 调用：prompt 构建、DeepSeek API 调用、响应解析。"""

from __future__ import annotations

import functools
import json
import os
import re
from string import Template
from typing import Any

import requests

from wx_obsidian.config import MAX_PROMPT_CONTENT, PROMPTS_DIR, SCRIPT_DIR, load_skill
from wx_obsidian.processing.models import ImageDescription

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------


@functools.cache
def load_prompt_template() -> Template:
    """加载 prompt 模板文件。"""
    template_file = PROMPTS_DIR / "summarize_article.txt"
    return Template(template_file.read_text(encoding="utf-8"))


def _build_images_context(
    image_descriptions: list[ImageDescription] | None,
    images_with_context: list[dict[str, str]] | None = None,
) -> str:
    """从图片描述列表构建注入 prompt 的上下文段落。

    Args:
        image_descriptions: Vision API 返回的图片描述列表。
        images_with_context: extract_images_with_context() 的原始结果，
            包含 url/before/after，用于告诉 LLM 每张图在文章中的位置。
    """
    if not image_descriptions:
        return ""
    content_images = [
        d
        for d in image_descriptions
        if d.is_content and d.status == "ok" and len(d.description) >= 10
    ]
    if not content_images:
        return ""
    # 构建 url -> before/after 的映射
    context_map: dict[str, dict[str, str]] = {}
    if images_with_context:
        for img in images_with_context:
            context_map[img.get("url", "")] = img

    lines = ["文章中的图片（按文章出现顺序排列）："]
    for i, desc in enumerate(content_images, 1):
        ctx = context_map.get(desc.url, {})
        before = ctx.get("before", "")[-120:]
        after = ctx.get("after", "")[:120]
        lines.append(f"[图片{i}] URL: {desc.url}")
        lines.append(f"  内容: {desc.description}")
        if before or after:
            lines.append(f"  位置: ...{before} [图片] {after}...")
    return "\n".join(lines)


def build_prompt(
    title: str,
    account_name: str,
    content: str,
    existing_articles: list[str],
    existing_concepts: list[str],
) -> str:
    """构建 Pass 1 的 prompt（纯文本，不包含图片）。"""
    body_style = load_skill("article-body")
    metadata_style = load_skill("note-metadata")
    classification_style = load_skill("classification")

    articles_str = "、".join(existing_articles[:100]) if existing_articles else "（暂无）"
    concepts_str = "、".join(existing_concepts[:100]) if existing_concepts else "（暂无）"

    template = load_prompt_template()
    return template.substitute(
        title=title,
        account_name=account_name,
        content=content[:MAX_PROMPT_CONTENT],
        body_style=body_style,
        classification_style=classification_style,
        metadata_style=metadata_style,
        articles_str=articles_str,
        concepts_str=concepts_str,
        images_context="",
    )


@functools.cache
def load_refine_prompt_template() -> Template:
    """加载 Pass 2 的 prompt 模板文件。"""
    template_file = PROMPTS_DIR / "refine_with_images.txt"
    return Template(template_file.read_text(encoding="utf-8"))


def _format_body_sections_as_markdown(body_sections: list[dict[str, Any]]) -> str:
    """将 body_sections 格式化为可读 markdown，让 LLM 清晰看到表格/代码块结构。"""
    parts: list[str] = []
    for section in body_sections:
        heading = section.get("heading", "")
        content = section.get("content", "")
        if heading:
            parts.append(f"## {heading}\n")
        parts.append(content)
        parts.append("")  # 章节间空行
    return "\n".join(parts)


def build_refine_prompt(
    article_content: str,
    body_sections: list[dict[str, Any]],
    image_descriptions: list[ImageDescription],
    images_with_context: list[dict[str, str]] | None = None,
) -> str:
    """构建 Pass 2 的 prompt（结合原文和图片描述修订正文）。"""
    images_context = _build_images_context(image_descriptions, images_with_context)
    body_md = _format_body_sections_as_markdown(body_sections)

    template = load_refine_prompt_template()
    return template.substitute(
        article_content=article_content[:MAX_PROMPT_CONTENT],
        body_sections=body_md,
        images_context=images_context,
    )


# ---------------------------------------------------------------------------
# API 响应解析
# ---------------------------------------------------------------------------


def _parse_api_response(text: str) -> dict[str, Any] | None:
    """从 API 响应中提取 JSON，处理常见的格式问题。"""
    if os.environ.get("DEBUG"):
        (SCRIPT_DIR / "last_response.txt").write_text(text, encoding="utf-8")

    # 去掉可能的 markdown 代码块标记
    clean = re.sub(r"```json\s*", "", text)
    clean = re.sub(r"```\s*$", "", clean.strip())

    json_match = re.search(r"\{[\s\S]*\}", clean)
    if not json_match:
        return None

    raw = json_match.group()
    try:
        result: dict[str, Any] = json.loads(raw, strict=False)
        return result
    except json.JSONDecodeError:
        try:
            result = json.loads(_fix_json_quotes(raw), strict=False)
            return result
        except json.JSONDecodeError as e:
            print(f"  JSON 解析失败: {e}")
            return None


def _fix_json_quotes(text: str) -> str:
    """修复 JSON 字符串内未转义的双引号。

    DeepSeek 有时在字符串值内返回未转义的 "xxx" 中文引号用法，
    导致 JSON 解析失败。用状态机跳过 JSON 结构引号，将值内的多余引号替换为单引号。
    """
    result: list[str] = []
    in_string = False
    i = 0

    while i < len(text):
        ch = text[i]

        # 转义字符，原样保留
        if ch == "\\" and in_string:
            result.append(text[i : i + 2])
            i += 2
            continue

        if ch == '"':
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                # 判断是否为字符串结束：后面应为 , } ] :
                rest = text[i + 1 : i + 10].lstrip()
                if rest and rest[0] in ",}]:":
                    in_string = False
                    result.append(ch)
                else:
                    # 值内的嵌套引号，替换为单引号
                    result.append("'")
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def _validate_images_field(images: list[Any]) -> list[dict[str, Any]]:
    """验证并清理 LLM 返回的 images 字段（Pass 2 输出）。

    只保留 valuable=True 且 URL 合法的项，确保 images 数组索引与 [IMG:N] 占位符一致。
    """
    valid: list[dict[str, Any]] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", ""))
        if not url or not url.startswith(("http://", "https://")):
            continue
        if not item.get("valuable", True):
            continue
        valid.append(
            {
                "url": url,
                "purpose": str(item.get("purpose", "")),
                "valuable": True,
            }
        )
    return valid


def _call_llm(
    prompt: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """调用 LLM API 并解析 JSON 响应。

    Args:
        config: 配置字典，优先从中读取 llm.model、llm.base_url。
            API key 始终从 os.environ 读取（由 .env 管理）。
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    llm_cfg = config.get("llm", {}) if config else {}
    base_url = llm_cfg.get(
        "base_url", os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    )
    model = llm_cfg.get("model", os.environ.get("MODEL_NAME", "deepseek-v4-pro"))

    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")

    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        print(f"  API 响应格式异常: {e}")
        return None
    return _parse_api_response(text)


def summarize_article(
    title: str,
    content: str,
    account_name: str,
    existing_articles: list[str] | None = None,
    existing_concepts: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pass 1：纯文本生成结构化笔记（不看图片）。"""
    prompt = build_prompt(
        title,
        account_name,
        content,
        existing_articles or [],
        existing_concepts or [],
    )
    return _call_llm(prompt, config=config)


def refine_with_images(
    article_content: str,
    body_sections: list[dict[str, Any]],
    image_descriptions: list[ImageDescription],
    images_with_context: list[dict[str, str]] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pass 2：结合原文和图片描述修订正文，返回含 [IMG:N] 占位符的 body_sections + images 数组。"""
    prompt = build_refine_prompt(
        article_content, body_sections, image_descriptions, images_with_context
    )
    result = _call_llm(prompt, config=config)
    if result and "images" in result:
        result["images"] = _validate_images_field(result["images"])
    return result
