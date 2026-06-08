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

# TUI mode (terminal UI)
python process_articles.py tui

# Cascade delete an article and all its associated data
python process_articles.py delete "文章标题或ID"

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
│   ├── validator.py       ← Markdown 格式校验与自动修复
│   └── cleanup.py         ← 文章级联删除（MOC、概念、归档、processed.json）
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
| `classification` | `skills/classification/SKILL.md` | category selection (11 presets), sub_topic extraction rules |
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

## Rules（编码红线）

以下规则不可违反，改完代码必须验证：

1. **验证闭环**：代码变更必须通过 `ruff check` + `mypy`，不允许用 `# type: ignore` 或禁用 ruff 规则来绕过
2. **路径安全**：LLM 返回的 category / sub_topic 必须经过 `re.sub(r'[<>:"/\\|?*]', "_", ...)` 过滤后才能用于文件路径
3. **原子写入**：持久化文件（processed.json）必须用 tempfile + os.replace 模式，防止中断导致数据损坏
4. **异常规范**：禁止裸 `except:`，必须指定异常类型；外部 API 调用必须有 timeout
5. **配置外置**：禁止硬编码路径、密钥、公众号名称，必须走 config.yaml 或 .env
6. **依赖隔离**：sources/、processing/、output/ 之间不互相依赖，通过 cli.py 编排

## Dev-Map（开发导航地图）

### 文件 → 职责 → 改动影响

| 文件 | 职责 | 改动时需关注 |
|------|------|-------------|
| `config.py` | 配置/持久化 | 被所有模块依赖，改动影响全局 |
| `sources/rss.py` | RSS 抓取 + 文章正文提取 | 返回值格式变更影响 cli.py |
| `processing/llm.py` | DeepSeek API 调用 + prompt 构建 | 改动 prompt 模板或响应解析影响 markdown.py |
| `processing/images.py` | 图片提取与插入 | 纯函数，改动影响 cli.py 中的调用 |
| `processing/markdown.py` | Markdown 生成 + 假图片清除 | 纯函数，输出格式影响 validator |
| `output/vault.py` | Obsidian Vault 操作 | 文件系统操作，改动影响 cli.py |
| `output/validator.py` | Markdown 格式校验与修复 | 校验规则变更可能影响已生成的文件 |
| `cli.py` | 流程编排 | 串联所有模块，改动需验证完整流程 |
| `prompts/summarize_article.txt` | Prompt 模板 | 改动直接影响 LLM 输出质量 |
| `skills/*/SKILL.md` | LLM 输出风格控制 | 改动影响所有新生成的笔记 |

### 常见开发任务 → 涉及文件

| 任务 | 涉及文件 |
|------|---------|
| 调整笔记输出格式 | `skills/article-body/SKILL.md`, `skills/note-metadata/SKILL.md` |
| 修改分类体系 | `skills/classification/SKILL.md`, `config.yaml` |
| 优化 LLM 提示词 | `prompts/summarize_article.txt`, `processing/llm.py` |
| 修复格式校验问题 | `output/validator.py` |
| 调整图片插入逻辑 | `processing/images.py` |
| 修改 Vault 目录结构 | `output/vault.py`, `config.yaml` |
| 新增数据源 | `sources/` 下新建模块, `cli.py` |
| 文章级联删除 | `output/cleanup.py`, `tui/screens/articles.py`, `process_articles.py` |

### 关键常量速查

| 常量 | 位置 | 值 | 说明 |
|------|------|---|------|
| `MAX_PROMPT_CONTENT` | config.py | 10000 | 发给 LLM 的最大字符数 |
| `MAX_ARTICLE_LENGTH` | config.py | 15000 | 文章最大长度 |
| `SUB_TOPIC_THRESHOLD` | config.py | 3 | 同主题几篇后创建子目录 |

## Development Notes

- Python 3.9+ required (uses `from __future__ import annotations` for type hints).
- Ruff config: line-length 100, target py39, E501 ignored (f-string prompt templates can't be split).
- `MAX_PROMPT_CONTENT = 10000` and `MAX_ARTICLE_LENGTH = 15000` cap content sent to the API.
- Path safety: LLM-returned category/sub_topic names are sanitized with `re.sub(r'[<>:"/\\|?*]', "_", ...)`.
- Images are inserted programmatically (not by LLM) via keyword-overlap matching between image context and markdown sections.
- `run.sh` sources `.env` from a sibling project (`~/Downloads/pyProj/MalogBot/.env`) and overrides `MODEL_NAME=deepseek-v4-pro`.

## Harness 开发范式

基于 Harness Engineering 理念，在 `.claude/skills/` 下建立了三层开发约束体系：

| Skill | Harness 层 | 触发方式 | 用途 |
|-------|-----------|---------|------|
| `coding-rules` | Rule（红线） | 自动：代码变更时 | 编码底线：路径安全、原子写入、异常规范、依赖隔离 |
| `verification` | Scripts（门禁） | 自动：提交前 | 质量验证：ruff check → ruff format → mypy → 格式校验 |
| `dev-workflow` | Workflow（流程） | 自动：收到开发任务时 | 5 阶段推进：理解→设计→实现→验证→提交 |

另有 `/spec` 命令（`.claude/commands/spec.md`）用于检查设计规格文档完整性。

### 落地逻辑

```
SPEC（明确做什么）→ Rule（红线约束）→ Skill（标准操作）→ Workflow（阶段推进）→ Scripts（验证闭环）
```

参考文章：《Harness Engineering工程化落地》
