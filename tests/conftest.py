"""共享 fixtures：临时目录、模拟数据、mock 工厂。"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path_factory, monkeypatch):
    """把「用户真实状态文件」全部重定向到临时目录（对每个测试自动生效）。

    这不是洁癖，是被真事故逼出来的：`tests/test_web/test_articles.py` 的删除测试
    会走 `cascade_delete` → `save_processed()`（默认路径），把 fixture 里 3 条假
    记录写进了用户真实的 `~/.wx-obsidian/processed.json`，抹掉 373 条去重账本。
    只补那一个 fixture 不够——这类「读被 mock 了、写没有」的组合还会再犯，
    所以在入口处统一掐断。
    """
    import wx_obsidian.config as config_module
    import wx_obsidian.processing.similarity as similarity_module

    sandbox = tmp_path_factory.mktemp("user-state")
    monkeypatch.setattr(config_module, "PROCESSED_FILE", sandbox / "processed.json")
    monkeypatch.setattr(similarity_module, "_DEFAULT_DB_PATH", sandbox / "similarity.sqlite")
    yield


@pytest.fixture
def tmp_vault(tmp_path: Path):
    """创建临时 Obsidian Vault 目录结构。"""
    articles_dir = tmp_vault_setup(tmp_path)
    return tmp_path, articles_dir


def tmp_vault_setup(vault_path: Path, articles_dir_name: str = "公众号文章") -> Path:
    """在 vault_path 下创建 articles 目录，返回 articles_dir 路径。"""
    articles_dir = vault_path / articles_dir_name
    articles_dir.mkdir(parents=True, exist_ok=True)
    return articles_dir


@pytest.fixture
def sample_config() -> dict:
    """返回最小可用 config 字典。"""
    return {
        "categories": ["LLM", "Agent架构", "其他"],
        "obsidian": {"vault_path": "/tmp/test_vault", "articles_dir": "公众号文章"},
        "source": {"type": "we_mp_rss", "base_url": "http://localhost:8001"},
    }


@pytest.fixture
def sample_processed() -> dict:
    """返回 processed.json 样例数据。"""
    return {
        "article_001": {
            "title": "测试文章1",
            "status": "done",
            "category": "LLM",
            "sub_topic": "注意力机制",
            "file": "/tmp/test_vault/公众号文章/LLM/测试文章1.md",
            "processed_at": "2026-01-01T00:00:00",
        },
        "article_002": {
            "title": "测试文章2",
            "status": "done",
            "category": "LLM",
            "sub_topic": "注意力机制",
            "file": "/tmp/test_vault/公众号文章/LLM/测试文章2.md",
            "processed_at": "2026-01-01T00:01:00",
        },
        "article_003": {
            "title": "跳过的文章",
            "status": "skipped",
            "reason": "no_content",
        },
    }


@pytest.fixture
def sample_html_with_images() -> str:
    """返回包含微信图片的 HTML 片段。"""
    return """
    <div id="js_content">
        <p>这是一段关于深度学习的文章内容。</p>
        <img data-src="https://mmbiz.qpic.cn/mmbiz_png/test_image_1.png"
             src="data:image/gif;base64,placeholder">
        <p>Transformer 架构的核心是注意力机制。</p>
        <img data-src="https://mmbiz.qpic.cn/mmbiz_jpg/test_image_2.jpg"
             src="data:image/gif;base64,placeholder">
        <p>多头注意力让模型能同时关注不同位置。</p>
        <img data-src="https://example.com/non_cdn.png"
             src="data:image/gif;base64,placeholder">
        <img data-src="https://mmbiz.qpic.cn/mmbiz_png/pic_blank.png"
             src="data:image/gif;base64,placeholder">
    </div>
    <script>var x = 1;</script>
    """


@pytest.fixture
def sample_llm_response() -> dict:
    """返回标准 LLM JSON 响应。"""
    return {
        "category": "LLM",
        "sub_topic": "注意力机制",
        "summary": "本文深度分析了 Transformer 的注意力机制，揭示了其核心设计哲学。",
        "key_points": [
            "**自注意力**：通过 Q/K/V 矩阵实现全局依赖建模",
            "**多头机制**：并行捕获不同子空间的特征",
        ],
        "concepts": [
            {
                "name": "Self-Attention",
                "description": "自注意力是一种通过查询-键-值机制计算序列内部依赖的方法",
            },
            {
                "name": "Multi-Head Attention",
                "description": "多头注意力通过并行多组注意力头捕获不同特征",
            },
        ],
        "tags": ["Transformer", "注意力机制", "深度学习"],
        "related_topics": ["Agent架构"],
        "body_sections": [
            {"heading": "一、核心概念与背景", "content": "注意力机制的核心是..."},
            {"heading": "二、核心机制拆解", "content": "Q/K/V 三个矩阵分别代表..."},
        ],
    }


@pytest.fixture
def sample_markdown() -> str:
    """返回标准 Obsidian markdown 内容。"""
    return """---
title: "测试文章"
source: "测试公众号"
author: "作者"
date: 2026-01-01
tags: [AI, 测试]
category: "LLM"
url: "https://example.com"
---

## 摘要
这是一篇测试文章的摘要。

## 核心观点
- **观点1**：核心内容1
- **观点2**：核心内容2

## 一、背景介绍
正文内容。

## 二、核心拆解
详细分析。

## 关键概念
- [[概念A]]：概念A的定义

## 相关主题
- [[已有文章]]

---
> 来源：测试公众号 | [原文链接](https://example.com)
"""
