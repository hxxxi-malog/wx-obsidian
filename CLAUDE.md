# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

wx-obsidian is a WeChat public account article → Obsidian knowledge base automation pipeline. It fetches articles from WeWe RSS, calls DeepSeek API to generate structured notes, and writes them into an Obsidian vault with bidirectional links and a knowledge graph.

## Commands

```bash
# Run the main pipeline (processes all unprocessed articles)
python process_articles.py

# Process limited articles
python process_articles.py --limit 5

# Lint
ruff check .

# Format
ruff format .

# Type check
mypy wx_obsidian/ process_articles.py validate_markdown.py

# Validate markdown files
python validate_markdown.py <file.md>
python validate_markdown.py --dir <directory>
```

## Architecture

Python package `wx_obsidian/` with three layers + CLI:

```
wx_obsidian/
├── config.py              ← 配置/持久化（.env, config.yaml, processed.json, skills）
├── sources/               ← 数据获取层
│   └── rss.py             ← WeWe RSS feed + 微信文章抓取
├── processing/            ← 处理层
│   ├── images.py          ← 图片提取 + 语义匹配 + 自动插入
│   ├── llm.py             ← DeepSeek API 调用 + prompt 模板 + 响应解析
│   └── markdown.py        ← Markdown 生成（frontmatter + body + 假图片清除）
├── output/                ← 输出层
│   ├── vault.py           ← Obsidian Vault 操作（MOC、概念页面、分类、子目录）
│   └── validator.py       ← Markdown 格式校验与自动修复
└── cli.py                 ← CLI 入口，编排完整流程
```

顶层入口：
- `process_articles.py` — 薄 CLI 入口，调用 `wx_obsidian.cli.main()`
- `validate_markdown.py` — 向后兼容入口，转发到 `wx_obsidian.output.validator`

### Dependency Flow

```
cli.py → sources/rss.py → processing/{images,llm,markdown}.py → output/{vault,validator}.py
           ↑                              ↑                              ↑
           └──────── config.py ───────────┴──────────────────────────────┘
```

- `config.py` 是最底层，被所有模块依赖
- `sources/`, `processing/`, `output/` 之间不互相依赖（通过 cli.py 编排）
- `processing/images.py` 和 `processing/markdown.py` 是纯函数模块

### Skill System

3 Skill files in `skills/` control the AI output style without code changes:

| Skill | Path | Controls |
|-------|------|----------|
| `article-body` | `skills/article-body/SKILL.md` | Chapter structure (4-6 sections), writing style, table usage |
| `classification` | `skills/classification/SKILL.md` | category selection (18 presets), sub_topic extraction rules |
| `note-metadata` | `skills/note-metadata/SKILL.md` | Frontmatter format, summary, concept extraction, related topics |

### Prompt Template

`prompts/summarize_article.txt` uses `string.Template` with `$variable` placeholders. Edit that file to change the prompt without touching Python code.

### Data Flow

```
WeWe RSS (localhost:4000) → fetch_articles() → JSON feed
    → summarize_article() → DeepSeek API + 3 Skills → structured JSON
    → generate_markdown() → Obsidian .md file
    → remove_non_cdn_images() + insert_images_into_markdown() → 图片自动插入
    → validate_and_fix() → 格式校验与修复
    → write file + update MOC + create concept pages + check sub-directory splitting
```

### Key State Files

- **`processed.json`** — Tracks processed article IDs to avoid reprocessing. Atomic saves after each article.
- **`config.yaml`** — Vault path, category list, WeWe RSS config. New categories appended at runtime (preserves comments).

## Configuration

- **`.env`** — API keys and runtime config (not committed). Copy from `.env.example`.
- **`config.yaml`** — Vault path (`vault_path`), articles directory name, category list.
- **`docker-compose.yml`** — Deploys WeWe RSS + MySQL for article fetching.

## Development Notes

- Python 3.9+ required (uses `from __future__ import annotations` for type hints).
- Ruff config: line-length 100, target py39, E501 ignored (f-string prompt templates can't be split).
- `MAX_PROMPT_CONTENT = 10000` and `MAX_ARTICLE_LENGTH = 15000` cap content sent to the API.
- Path safety: LLM-returned category/sub_topic names are sanitized with `re.sub(r'[<>:"/\\|?*]', "_", ...)`.
- Images are inserted programmatically (not by LLM) via keyword-overlap matching between image context and markdown sections.
- `run.sh` sources `.env` from a sibling project (`~/Downloads/pyProj/MalogBot/.env`) and overrides `MODEL_NAME=deepseek-v4-pro`.
