"""图片处理：从 HTML 提取图片、匹配到 Markdown 章节、自动插入。"""

from __future__ import annotations

import html
import re

# ---------------------------------------------------------------------------
# 预编译正则
# ---------------------------------------------------------------------------

RE_IMG_TAG = re.compile(r"(<img\s[^>]+>)")
RE_IMG_TAG_START = re.compile(r"<img\s")
RE_DATA_SRC = re.compile(r'data-src=["\']([^"\']+)["\']')
RE_SRC = re.compile(r'src=["\']([^"\']+)["\']')
RE_HTML_TAG = re.compile(r"<[^>]+>")
RE_WHITESPACE = re.compile(r"\s+")

RE_SECTION_HEAD = re.compile(r"(?=^## )", re.MULTILINE)
RE_BODY_HEADING = re.compile(r"^## (?:[一二三四五六七八九十]+[、.]|[一二三四五六七八九十]+ |[0-9]+[.、]|[0-9]+ )")
RE_CN_KEYWORD = re.compile(r"[一-鿿]{2,6}")
RE_EN_KEYWORD = re.compile(r"[a-zA-Z]{3,}")


# ---------------------------------------------------------------------------
# 公开函数
# ---------------------------------------------------------------------------


def extract_images_with_context(html_text: str, max_images: int = 8) -> list[dict[str, str]]:
    """从 HTML 中提取图片 URL 及其前后文字上下文。

    返回列表，每项包含 url、before（图片前的文字）、after（图片后的文字）。
    """
    parts = RE_IMG_TAG.split(html_text)
    results: list[dict[str, str]] = []

    for i, part in enumerate(parts):
        if not RE_IMG_TAG_START.match(part):
            continue
        # 提取 URL（优先 data-src，回退 src）
        url_match = RE_DATA_SRC.search(part)
        if not url_match:
            url_match = RE_SRC.search(part)
        if not url_match:
            continue
        url = html.unescape(url_match.group(1))
        if not url.startswith("http"):
            continue
        # 只保留微信 CDN 的图片
        if "mmbiz" not in url:
            continue
        # 跳过空白占位图
        if "pic_blank" in url:
            continue

        # 提取前后各 200 字的纯文本上下文
        before_text = RE_HTML_TAG.sub(" ", parts[i - 1] if i > 0 else "")
        after_text = RE_HTML_TAG.sub(" ", parts[i + 1] if i + 1 < len(parts) else "")
        before_text = RE_WHITESPACE.sub(" ", before_text).strip()[-200:]
        after_text = RE_WHITESPACE.sub(" ", after_text).strip()[:200]

        results.append({"url": url, "before": before_text, "after": after_text})
        if len(results) >= max_images:
            break

    return results


def insert_images_into_markdown(md: str, images: list[dict[str, str]]) -> str:
    """将图片自动匹配到 markdown 的对应章节中。

    匹配策略：对每张图片，用其前后文字与各章节内容做关键词重叠度评分，
    插入得分最高的章节的第一个段落之后。
    """
    if not images:
        return md

    # 按 ## 标题拆分 markdown 为 section
    raw_sections = RE_SECTION_HEAD.split(md)

    # 预计算每个 section 的关键词
    section_keywords: list[set[str]] = []
    for sec in raw_sections:
        section_keywords.append(_extract_keywords(sec))

    # 识别正文章节（## 一、 ## 1. 等），只在这些章节中插入图片
    body_section_indices: list[int] = []
    for idx, sec in enumerate(raw_sections):
        heading = sec.split("\n")[0].strip()
        if RE_BODY_HEADING.match(heading):
            body_section_indices.append(idx)

    if not body_section_indices:
        return md

    # 每张图片最多插一次，每个 section 最多插两张
    section_image_count = [0] * len(raw_sections)
    used_images: set[int] = set()

    for img_idx, img in enumerate(images):
        if img_idx in used_images:
            continue
        img_text = img["before"] + " " + img["after"]
        img_kw = _extract_keywords(img_text)
        if not img_kw:
            continue

        best_score = 0
        best_sec = -1
        for sec_idx in body_section_indices:
            if section_image_count[sec_idx] >= 2:
                continue
            overlap = len(img_kw & section_keywords[sec_idx])
            if overlap > best_score:
                best_score = overlap
                best_sec = sec_idx

        if best_sec < 1 or best_score < 2:
            continue

        # 在该 section 的第一个段落之后插入图片
        sec = raw_sections[best_sec]
        lines = sec.split("\n")
        insert_pos = 0
        for j, line in enumerate(lines):
            if j < 2:
                continue  # 跳过标题行
            if line.strip() and not line.startswith("#"):
                insert_pos = j + 1
                break

        desc = _truncate_description(img["before"])
        img_md = f"\n![{desc}]({img['url']})"
        lines.insert(insert_pos, img_md)
        raw_sections[best_sec] = "\n".join(lines)
        section_image_count[best_sec] += 1
        used_images.add(img_idx)

    return "".join(raw_sections)


# ---------------------------------------------------------------------------
# 内部函数
# ---------------------------------------------------------------------------


def _extract_keywords(text: str) -> set[str]:
    """从文本中提取关键词（中文 2-6 字符，英文 3+ 字符）。"""
    cn = set(RE_CN_KEYWORD.findall(text))
    en = set(w.lower() for w in RE_EN_KEYWORD.findall(text))
    return cn | en


def _truncate_description(before_text: str) -> str:
    """从图片前的文字中截取有意义的描述。"""
    desc = before_text[-50:].strip()
    # 去掉开头不完整的片段
    for sep in ("。", "，", "；", ".", ",", " "):
        idx = desc.find(sep)
        if 0 < idx < len(desc) - 5:
            desc = desc[idx + 1 :].strip()
            break
    if len(desc) > 40:
        desc = desc[:40]
    return desc
