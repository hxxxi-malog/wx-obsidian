#!/usr/bin/env python3
"""公众号文章 → Obsidian 知识库处理器。

从 WeWe RSS 拉取公众号文章，调用 DeepSeek API 生成结构化笔记，
写入 Obsidian Vault 并自动维护 MOC、概念页面和子目录。
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests
import yaml
from string import Template

from validate_markdown import validate_and_fix

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
SKILLS_DIR = SCRIPT_DIR / "skills"
PROMPTS_DIR = SCRIPT_DIR / "prompts"
PROCESSED_FILE = SCRIPT_DIR / "processed.json"
MAX_ARTICLE_LENGTH = 15000
MAX_PROMPT_CONTENT = 10000
SUB_TOPIC_THRESHOLD = 3

# 加载 .env 文件（不覆盖已有的环境变量）
_ENV_FILE = SCRIPT_DIR / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _key, _, _value = _line.partition("=")
        _key, _value = _key.strip(), _value.strip()
        if _key not in os.environ:
            os.environ[_key] = _value


# ---------------------------------------------------------------------------
# 配置与持久化
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """加载 config.yaml 配置。"""
    with open(SCRIPT_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_processed() -> dict[str, Any]:
    """加载已处理文章记录。"""
    if PROCESSED_FILE.exists():
        try:
            return json.loads(PROCESSED_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"警告: processed.json 解析失败 ({e})，将重新开始")
            return {}
    return {}


def save_processed(processed: dict[str, Any]) -> None:
    """保存已处理文章记录。"""
    PROCESSED_FILE.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")


@functools.cache
def load_skill(name: str) -> str:
    """加载 skill 文件内容，去掉 YAML frontmatter。"""
    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        return ""
    text = skill_file.read_text(encoding="utf-8")
    parts = text.split("---", 2)
    return parts[2].strip() if len(parts) >= 3 else ""


# ---------------------------------------------------------------------------
# 知识库扫描
# ---------------------------------------------------------------------------


def scan_existing_content(vault_path: Path, articles_dir_name: str) -> tuple[list[str], list[str]]:
    """扫描知识库中已有的文章和概念，用于相关主题关联。"""
    articles_base = vault_path / articles_dir_name
    existing_articles: list[str] = []
    existing_concepts: list[str] = []

    if articles_base.exists():
        for category_dir in articles_base.iterdir():
            if not category_dir.is_dir() or category_dir.name.startswith("."):
                continue
            for md_file in category_dir.glob("*.md"):
                if md_file.name != "_MOC.md":
                    existing_articles.append(md_file.stem)

    concept_dir = articles_base / "概念"
    if concept_dir.exists():
        for md_file in concept_dir.glob("*.md"):
            if md_file.name != "_MOC.md":
                existing_concepts.append(md_file.stem)

    return existing_articles, existing_concepts


# ---------------------------------------------------------------------------
# HTML 解析
# ---------------------------------------------------------------------------


class HTMLTextExtractor(HTMLParser):
    """从 HTML 中提取纯文本内容。"""

    def __init__(self) -> None:
        super().__init__()
        self._text: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "div", "br", "h1", "h2", "h3", "h4", "li", "tr"):
            self._text.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._text.append(data.strip())

    def get_text(self) -> str:
        """返回提取的纯文本。"""
        return "\n".join(line for line in "".join(self._text).splitlines() if line.strip())


# ---------------------------------------------------------------------------
# 文章抓取
# ---------------------------------------------------------------------------


def fetch_article_content_from_url(url: str) -> str:
    """从微信文章 URL 抓取正文内容。"""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.encoding = "utf-8"

        html = resp.text
        match = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', html, re.DOTALL)
        body_html = match.group(1) if match else html

        parser = HTMLTextExtractor()
        parser.feed(body_html)
        return parser.get_text()[:MAX_ARTICLE_LENGTH]
    except requests.RequestException as e:
        print(f"  抓取正文失败: {e}")
        return ""


def fetch_articles(config: dict[str, Any]) -> list[dict[str, Any]]:
    """从 WeWe RSS JSON Feed 获取文章列表。"""
    base_url = config["wewe_rss"]["base_url"]
    resp = requests.get(f"{base_url}/feeds/all.json", timeout=15)
    resp.raise_for_status()
    feed = resp.json()

    all_articles: list[dict[str, Any]] = []
    for item in feed.get("items", []):
        author_info = item.get("author", {})
        author_name = (
            author_info.get("name", "") if isinstance(author_info, dict) else str(author_info)
        )
        all_articles.append(
            {
                "id": item.get("id", item.get("url", "")),
                "title": item.get("title", "无标题"),
                "url": item.get("url", item.get("external_url", "")),
                "content": item.get("content_html", item.get("content_text", "")),
                "date_published": item.get("date_published", ""),
                "author": author_name,
                "_account_name": author_name or "未知",
            }
        )

    return all_articles


# ---------------------------------------------------------------------------
# DeepSeek API 调用
# ---------------------------------------------------------------------------


@functools.cache
def _load_prompt_template() -> Template:
    """加载 prompt 模板文件。"""
    template_file = PROMPTS_DIR / "summarize_article.txt"
    return Template(template_file.read_text(encoding="utf-8"))


def _build_prompt(
    title: str,
    account_name: str,
    content: str,
    config: dict[str, Any],
    existing_articles: list[str],
    existing_concepts: list[str],
    images: list[str] | None = None,
) -> str:
    """构建发送给 DeepSeek 的 prompt。"""
    body_style = load_skill("article-body")
    metadata_style = load_skill("note-metadata")
    classification_style = load_skill("classification")

    articles_str = "、".join(existing_articles[:100]) if existing_articles else "（暂无）"
    concepts_str = "、".join(existing_concepts[:100]) if existing_concepts else "（暂无）"

    images_section = ""
    if images:
        images_list = "\n".join(f"- {url}" for url in images[:10])
        images_section = f"""
## 原文图片（必须在笔记中引用）

以下是原文中的图片 URL。**你必须在 body_sections 的 content 中用 `![描述](URL)` 引用至少 2-3 张图片**（架构图、流程图、关键示意图优先）：
{images_list}

规则：直接使用上方 URL，不要编造或修改。图片放在描述相关架构或流程的文字之后。
"""

    template = _load_prompt_template()
    return template.substitute(
        title=title,
        account_name=account_name,
        content=content[:MAX_PROMPT_CONTENT],
        body_style=body_style,
        classification_style=classification_style,
        metadata_style=metadata_style,
        images_section=images_section,
        articles_str=articles_str,
        concepts_str=concepts_str,
    )


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

    DeepSeek 有时在字符串值内返回未转义的 \"xxx\" 中文引号用法，
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


def summarize_article(
    config: dict[str, Any],
    title: str,
    content: str,
    account_name: str,
    existing_articles: list[str] | None = None,
    existing_concepts: list[str] | None = None,
    images: list[str] | None = None,
) -> dict[str, Any] | None:
    """调用 DeepSeek API 总结文章，返回结构化 JSON。"""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("MODEL_NAME", "deepseek-v4-pro")

    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未设置")

    prompt = _build_prompt(
        title,
        account_name,
        content,
        config,
        existing_articles or [],
        existing_concepts or [],
        images=images,
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


# ---------------------------------------------------------------------------
# Markdown 生成
# ---------------------------------------------------------------------------


def _build_frontmatter(
    title: str,
    account_name: str,
    author: str,
    date: str,
    url: str,
    category: str,
    sub_topic: str,
    tags: list[str],
) -> str:
    """构建 YAML frontmatter。"""
    tags_str = ", ".join(t.replace(" ", "_") for t in tags)
    sub_topic_line = f'\nsub_topic: "{sub_topic}"' if sub_topic else ""
    return f"""---
title: "{title.replace('"', "'")}"
source: "{account_name}"
author: "{author or account_name}"
date: {date}
tags: [{tags_str}]
category: "{category}"{sub_topic_line}
url: "{url}"
---"""


def generate_markdown(
    title: str,
    account_name: str,
    author: str,
    date: str,
    url: str,
    summary_data: dict[str, Any],
) -> str:
    """生成 Obsidian Markdown 文件内容。"""
    category = summary_data.get("category", "其他")
    sub_topic = summary_data.get("sub_topic", "")
    summary = summary_data.get("summary", "")
    key_points: list[str] = summary_data.get("key_points", [])
    concepts: list[dict[str, str]] = summary_data.get("concepts", [])
    tags: list[str] = summary_data.get("tags", [])
    related: list[str] = summary_data.get("related_topics", [])
    body_sections: list[dict[str, str]] = summary_data.get("body_sections", [])

    frontmatter = _build_frontmatter(
        title, account_name, author, date, url, category, sub_topic, tags
    )

    points_md = "\n".join(f"- {p}" for p in key_points)
    concepts_md = "\n".join(f"- [[{c['name']}]]：{c.get('description', '')}" for c in concepts)
    related_md = "\n".join(f"- [[{r}]]" for r in related)

    body_parts: list[str] = []
    for section in body_sections:
        heading = section.get("heading", "")
        body_content = section.get("content", "")
        body_parts.append(f"\n## {heading}\n\n{body_content}\n")
    body_md = "".join(body_parts)

    return f"""{frontmatter}

## 摘要
{summary}

## 核心观点
{points_md}
{body_md}
## 关键概念
{concepts_md}

## 相关主题
{related_md}

---
> 来源：{account_name} | [原文链接]({url})
"""


# ---------------------------------------------------------------------------
# Obsidian Vault 操作
# ---------------------------------------------------------------------------


def ensure_concept_page(
    vault_path: Path, concept_name: str, description: str, articles_dir: Path | None = None
) -> None:
    """确保概念页面存在，不存在则创建。"""
    base = articles_dir or (vault_path / "公众号文章")
    concept_dir = base / "概念"
    concept_file = concept_dir / f"{concept_name}.md"

    if not concept_file.exists():
        content = f"""---
tags: [概念]
---

# {concept_name}

{description}

## 相关文章
> 自动更新
"""
        concept_file.write_text(content, encoding="utf-8")


def update_moc(
    vault_path: Path, category: str, title: str, date: str, articles_dir: Path | None = None
) -> None:
    """更新分类 MOC 文件，追加新文章链接。"""
    base = articles_dir or (vault_path / "公众号文章")
    moc_file = base / category / "_MOC.md"
    if not moc_file.exists():
        return

    content = moc_file.read_text(encoding="utf-8")
    entry = f"- {date} [[{title}]]"
    if entry not in content:
        content = content.rstrip() + f"\n{entry}"
        moc_file.write_text(content, encoding="utf-8")


def ensure_category(
    vault_path: Path, config: dict[str, Any], category: str, articles_dir: Path | None = None
) -> None:
    """确保分类存在：创建目录、MOC 文件，并更新 config.yaml。"""
    if category in config["categories"]:
        return

    print(f"  发现新分类：{category}，自动创建...")

    base = articles_dir or (vault_path / "公众号文章")
    category_dir = base / category
    category_dir.mkdir(parents=True, exist_ok=True)

    moc_file = category_dir / "_MOC.md"
    if not moc_file.exists():
        moc_file.write_text(f"# {category}\n\n", encoding="utf-8")

    config["categories"].append(category)
    config_path = SCRIPT_DIR / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)

    root_moc = base / "_MOC.md"
    if root_moc.exists():
        content = root_moc.read_text(encoding="utf-8")
        if f"[[{category}]]" not in content:
            content = content.rstrip() + f"\n- [[{category}]]"
            root_moc.write_text(content, encoding="utf-8")


def count_sub_topic_articles(processed: dict[str, Any], category: str, sub_topic: str) -> int:
    """统计同一分类下同一子主题的文章数量。"""
    return sum(
        1
        for record in processed.values()
        if record.get("status") == "done"
        and record.get("category") == category
        and record.get("sub_topic") == sub_topic
    )


def maybe_create_subcategory(
    vault_path: Path,
    config: dict[str, Any],
    processed: dict[str, Any],
    category: str,
    sub_topic: str,
) -> None:
    """当同一子主题积累足够文章时，创建子目录并迁移文件。"""
    if not sub_topic:
        return

    count = count_sub_topic_articles(processed, category, sub_topic)
    if count < SUB_TOPIC_THRESHOLD:
        return

    articles_dir = vault_path / config["obsidian"]["articles_dir"]
    sub_dir = articles_dir / category / sub_topic

    if sub_dir.exists():
        return

    print(f"  子主题「{sub_topic}」已有 {count} 篇文章，创建子目录 {category}/{sub_topic}/")
    sub_dir.mkdir(parents=True, exist_ok=True)

    moc_file = sub_dir / "_MOC.md"
    if not moc_file.exists():
        moc_file.write_text(f"# {sub_topic}\n\n", encoding="utf-8")

    _migrate_articles_to_subdir(processed, category, sub_topic, articles_dir, sub_dir, moc_file)
    _update_parent_moc(articles_dir, category, sub_topic)


def _migrate_articles_to_subdir(
    processed: dict[str, Any],
    category: str,
    sub_topic: str,
    articles_dir: Path,
    sub_dir: Path,
    moc_file: Path,
) -> None:
    """将已有文章迁移到子目录。"""
    for _article_id, record in processed.items():
        if (
            record.get("status") != "done"
            or record.get("category") != category
            or record.get("sub_topic") != sub_topic
        ):
            continue

        old_path = Path(record.get("file", ""))
        if not old_path.exists() or old_path.parent != articles_dir / category:
            continue

        new_path = sub_dir / old_path.name
        old_path.rename(new_path)
        record["file"] = str(new_path)

        # 更新文件内的 category 字段
        content = new_path.read_text(encoding="utf-8")
        old_cat = f'category: "{category}"'
        new_cat = f'category: "{category}/{sub_topic}"'
        if old_cat in content:
            new_path.write_text(content.replace(old_cat, new_cat), encoding="utf-8")

        # 更新子目录 MOC
        safe_title = old_path.stem
        entry = f"- {record.get('processed_at', '')[:10]} [[{safe_title}]]"
        moc_content = moc_file.read_text(encoding="utf-8")
        if entry not in moc_content:
            moc_file.write_text(moc_content.rstrip() + f"\n{entry}", encoding="utf-8")


def _update_parent_moc(articles_dir: Path, category: str, sub_topic: str) -> None:
    """在父分类 MOC 中添加子目录链接。"""
    parent_moc = articles_dir / category / "_MOC.md"
    if not parent_moc.exists():
        return

    content = parent_moc.read_text(encoding="utf-8")
    if f"[[{sub_topic}]]" not in content:
        content = content.rstrip() + f"\n- 📁 [[{sub_topic}]]"
        parent_moc.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 单篇文章处理
# ---------------------------------------------------------------------------


def _extract_article_info(article: dict[str, Any]) -> dict[str, str]:
    """从原始文章数据中提取标准化字段。"""
    date = article.get("date_published", "") or ""
    if isinstance(date, str) and len(date) > 10:
        date = date[:10]
    if not date:
        date = datetime.now().strftime("%Y-%m-%d")

    return {
        "id": str(article["id"]),
        "title": article.get("title", "无标题"),
        "account_name": article.get("_account_name", "未知"),
        "author": article.get("author", ""),
        "date": date,
        "url": article.get("url", ""),
    }


def _extract_images(content: str) -> list[str]:
    """从 HTML 内容中提取图片 URL 列表（优先 data-src，回退 src）。"""
    urls: list[str] = []
    for match in re.finditer(r"<img\s[^>]+>", content):
        tag = match.group(0)
        # 优先取 data-src（微信懒加载），没有则取 src
        url_match = re.search(r'data-src=["\']([^"\']+)["\']', tag)
        if not url_match:
            url_match = re.search(r'src=["\']([^"\']+)["\']', tag)
        if url_match:
            url = url_match.group(1).replace("&amp;", "&")
            if url.startswith("http"):
                urls.append(url)
    return urls


def _extract_content(article: dict[str, Any]) -> tuple[str, list[str]]:
    """提取并清理文章正文内容，返回 (纯文本, 图片URL列表)。"""
    raw_content = article.get("content", "")
    images = _extract_images(raw_content)
    content = re.sub(r"<[^>]+>", " ", raw_content)
    content = re.sub(r"\s+", " ", content).strip()

    if len(content) < 50:
        url = article.get("url", "")
        if url:
            print("  Feed 无内容，从 URL 抓取...")
            content = fetch_article_content_from_url(url)

    return content, images


def _process_single_article(
    article: dict[str, Any],
    config: dict[str, Any],
    processed: dict[str, Any],
    vault_path: Path,
    articles_dir: Path,
    existing_articles: list[str],
    existing_concepts: list[str],
) -> None:
    """处理单篇文章：抓取 → 总结 → 生成 → 写入。"""
    info = _extract_article_info(article)
    article_id = info["id"]

    print(f"\n处理: [{info['account_name']}] {info['title']}")

    content, images = _extract_content(article)
    if len(content) < 50:
        print("  跳过：内容过短或为空")
        processed[article_id] = {
            "title": info["title"],
            "status": "skipped",
            "reason": "no_content",
        }
        return

    # DeepSeek 总结
    try:
        summary_data = summarize_article(
            config,
            info["title"],
            content,
            info["account_name"],
            existing_articles,
            existing_concepts,
            images=images,
        )
    except (requests.RequestException, ValueError) as e:
        print(f"  DeepSeek API 调用失败: {e}")
        processed[article_id] = {"title": info["title"], "status": "error", "reason": str(e)}
        return

    if not summary_data:
        print("  总结解析失败")
        processed[article_id] = {
            "title": info["title"],
            "status": "error",
            "reason": "parse_failed",
        }
        return

    # 写入文件（过滤路径安全字符）
    category = re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("category", "其他"))
    sub_topic = (
        re.sub(r'[<>:"/\\|?*]', "_", summary_data.get("sub_topic", ""))
        if summary_data.get("sub_topic")
        else ""
    )
    ensure_category(vault_path, config, category, articles_dir)

    md_content = generate_markdown(
        info["title"],
        info["account_name"],
        info["author"],
        info["date"],
        info["url"],
        summary_data,
    )

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", info["title"])[:100]
    category_dir = articles_dir / category
    category_dir.mkdir(parents=True, exist_ok=True)
    file_path = category_dir / f"{safe_title}.md"

    md_content, format_issues = validate_and_fix(md_content)
    if format_issues:
        print(f"  格式校验: {len(format_issues)} 个问题已修复")

    file_path.write_text(md_content, encoding="utf-8")

    # 更新关联数据
    for concept in summary_data.get("concepts", []):
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", concept["name"])
        ensure_concept_page(vault_path, safe_name, concept.get("description", ""), articles_dir)

    update_moc(vault_path, category, safe_title, info["date"], articles_dir)

    processed[article_id] = {
        "title": info["title"],
        "status": "done",
        "category": category,
        "sub_topic": sub_topic,
        "file": str(file_path),
        "processed_at": datetime.now().isoformat(),
    }

    maybe_create_subcategory(vault_path, config, processed, category, sub_topic)
    print(f"  完成 → {category}/{safe_title}.md")


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="公众号文章 → Obsidian 知识库处理器")
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 篇文章（0=不限制）")
    return parser.parse_args()


def main() -> None:
    """主流程：拉取文章 → 总结 → 写入 Obsidian。"""
    args = _parse_args()

    config = load_config()
    processed = load_processed()
    vault_path = Path(config["obsidian"]["vault_path"])
    articles_dir = vault_path / config["obsidian"]["articles_dir"]

    print("正在从 WeWe RSS 获取文章...")
    try:
        articles = fetch_articles(config)
    except requests.RequestException as e:
        print(f"获取文章失败: {e}")
        print("请确认 WeWe RSS 已启动 (http://localhost:4000) 并已登录微信读书")
        sys.exit(1)

    new_articles = [a for a in articles if a.get("id") and str(a["id"]) not in processed]
    if args.limit > 0:
        new_articles = new_articles[: args.limit]
    print(f"共获取 {len(articles)} 篇文章，其中 {len(new_articles)} 篇待处理")

    existing_articles_list, existing_concepts_list = scan_existing_content(
        vault_path, config["obsidian"]["articles_dir"]
    )

    for article in new_articles:
        _process_single_article(
            article,
            config,
            processed,
            vault_path,
            articles_dir,
            existing_articles_list,
            existing_concepts_list,
        )
        save_processed(processed)
        time.sleep(1)
    done_count = sum(1 for v in processed.values() if v.get("status") == "done")
    print(f"\n处理完成！共处理 {done_count} 篇文章")


if __name__ == "__main__":
    main()
