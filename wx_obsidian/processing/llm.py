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

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------


@functools.cache
def load_prompt_template() -> Template:
    """加载 prompt 模板文件。"""
    template_file = PROMPTS_DIR / "summarize_article.txt"
    return Template(template_file.read_text(encoding="utf-8"))


def build_prompt(
    title: str,
    account_name: str,
    content: str,
    existing_articles: list[str],
    existing_concepts: list[str],
) -> str:
    """构建发送给 DeepSeek 的 prompt。"""
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
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        try:
            return json.loads(_fix_json_quotes(raw), strict=False)
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


def summarize_article(
    title: str,
    content: str,
    account_name: str,
    existing_articles: list[str] | None = None,
    existing_concepts: list[str] | None = None,
) -> dict[str, Any] | None:
    """调用 DeepSeek API 总结文章，返回结构化 JSON。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("MODEL_NAME", "deepseek-v4-pro")

    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")

    prompt = build_prompt(
        title,
        account_name,
        content,
        existing_articles or [],
        existing_concepts or [],
    )

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
