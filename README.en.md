# wx-obsidian

<p align="center">
  <a href="README.md">简体中文</a> ·
  <a href="README.en.md"><b>English</b></a>
</p>

<p align="center">
  <a href="https://github.com/hxxxi-malog/wx-obsidian/actions/workflows/ci.yml"><img src="https://github.com/hxxxi-malog/wx-obsidian/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <a href="https://github.com/hxxxi-malog/wx-obsidian/releases"><img src="https://img.shields.io/github/v/release/hxxxi-malog/wx-obsidian" alt="Release"></a>
  <img src="https://img.shields.io/badge/Python-3.12+-blue" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/DeepSeek-API-orange" alt="DeepSeek">
  <img src="https://img.shields.io/badge/we--mp--rss-Docker-green" alt="we-mp-rss">
  <img src="https://img.shields.io/badge/Obsidian-Vault-purple" alt="Obsidian">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="MIT">
</p>

An automated pipeline from WeChat public-account articles to an Obsidian knowledge base: it fetches articles from [we-mp-rss](https://github.com/rachelos/we-mp-rss), has DeepSeek generate structured notes, writes them into your Obsidian vault, and keeps bidirectional links and the knowledge graph up to date.
Built for people who want to keep long-form WeChat articles in their own Obsidian vault without copying, pasting and reformatting by hand.
Both a CLI and a Web UI (browser) ship on top of the same orchestrator; a single `docker compose up -d --build` gets everything running.

<p align="center">
  <img src="assets/web-dashboard.png" alt="Dashboard: connectivity checks for the data source / LLM / Vision / vault, processing statistics and category distribution" width="860">
</p>

## Table of Contents

- [Features](#features)
- [What a Generated Note Looks Like](#what-a-generated-note-looks-like)
- [Quick Start](#quick-start)
- [Running Modes](#running-modes)
- [Advanced Docker Deployment](#advanced-docker-deployment)
- [Configuration](#configuration)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Customizing the Output](#customizing-the-output)
- [FAQ](#faq)
- [Development](#development)
- [Disclaimer](#disclaimer)
- [Acknowledgements](#acknowledgements)
- [License](#license)

## Features

- **One orchestrator, two front ends**: batch processing from the command line and a FastAPI + Vue browser UI
- **Latest-articles timeline**: browse articles from every subscribed account in reverse publish order, open the original with one click, filter by account, load more page by page; articles that already have a note are tagged "in vault", while ones missing a body are tagged "fetching" / "pending" / "fetch failed", and failed ones can be retried in place
- **Automatic body backfill**: collection first asks the WeChat Reading content API, falls back to fetching the original WeChat page when the session is dead or the article is an image message, and starts backfilling the remaining articles as soon as a collection round ends instead of waiting for the upstream hourly queue
- **Data-source login recoverable in the UI**: the Account page really verifies the WeChat Reading credential (cover API and content API checked separately); when the session is dead but bodies are already served by the WeChat fallback it warns instead of interrupting, and when the credential is truly gone you can scan a new QR code there, after which the app triggers a fetch for every subscribed account
- **Incremental fetching**: `processed.json` deduplicates articles, so you never pay twice for the same one
- **Parallel + retries**: ThreadPoolExecutor concurrency with exponential backoff (up to 3 attempts, covering 429 / 5xx / network errors)
- **Multimodal Vision**: a vision model enriches images with semantic descriptions for the LLM; when Vision is unavailable the pipeline degrades gracefully to plain text + keyword-based image matching instead of failing
- **Change the note style by editing a Skill**: section structure, classification rules and frontmatter are controlled by 3 Skill files — no code changes needed
- **Links and graph, generated automatically**: `[[bidirectional links]]` + concept pages + MOC; article-to-article similarity uses SQLite FTS5/BM25F + jieba + fuzzy concept matching + tag Jaccard, fused by RRF ranking
- **Three layers of format guarantees**: automatic validation & repair → mistune structural checks → LLM feedback correction
- **Cascade delete / switchable model providers**: one command removes an article and all of its derived data; add a provider in `config.json` to switch models
- **One command to run it all**: the data source (derived we-mp-rss image) and this project's web service come up together in a single compose project

## What a Generated Note Looks Like

The output is in Chinese because the pipeline is tuned for Chinese articles — note the frontmatter, the `[[wiki-links]]` and the generated sections:

```markdown
---
title: "Claude Code 工程架构深度拆解"
source: "AI前线"
author: "张三"
date: 2026-06-04
tags: [Claude_Code, AI_Agent, 工程化, 上下文管理]
category: "Agent"
sub_topic: "Agent Loop"
url: "https://mp.weixin.qq.com/..."
---

## 摘要
本文深度拆解 Claude Code 的工程架构，核心论点是其竞争力源于 12 层渐进式包装……

## 核心观点
- **胖核心设计**：query.ts 刻意保持单文件，保证核心循环原子性
- **开闭原则**：所有扩展不修改核心循环，新增功能 = 新增工具 / 包装层

## 一、核心架构
Claude Code 采用了「胖核心」设计：query.ts 刻意保持单文件，保证核心循环的原子性……

![架构全景图](https://mmbiz.qpic.cn/...)

## 关键概念
- [[Progressive Harness]]：渐进式工程包装，在极简内核外逐层叠加生产级特性
- [[Context Engineering]]：构建高信噪比上下文供给系统的工程方法论

## 相关主题
- [[RAG]]
```

> Source information (`source`, `url`) already lives in the frontmatter, so the body doesn't repeat it.

## Quick Start

Prerequisites:

- Docker and Docker Compose v2.20+ (the compose file uses `include`; check your version with `docker compose version`)
- A [DeepSeek API key](https://platform.deepseek.com/) (any OpenAI-compatible provider works too — see [Configuration](#configuration))
- An Obsidian vault directory (you'll fill in its path on the Web settings page)

Not using Docker? Use the source-code path (Python 3.12+), see [Running Modes](#running-modes).

**1. Start the services**

```bash
git clone https://github.com/hxxxi-malog/wx-obsidian.git
cd wx-obsidian

# Data source backend credentials
cp deploy/we-mp-rss/.env.example deploy/we-mp-rss/.env     # fill in WERSS_USER / WERSS_PASS

# Web access token (leave empty to get a random token on every restart; bookmark URLs will break)
cp deploy/wx-obsidian/.env.example deploy/wx-obsidian/.env
openssl rand -base64 24                                    # paste this into WX_WEB_TOKEN=

docker compose up -d --build
docker compose ps                                          # both services should be Up and (healthy)
```

By default the compose file mounts `~/Documents/公众号文章` — the **articles directory** derived from `obsidian.vault_path` + `articles_dir`, mounted at **the same absolute path** inside and outside the container (`processed.json` stores absolute paths). If your articles live elsewhere, `export WX_VAULT_DIR=/path/to/公众号文章` before starting.

**2. Initialize the data source**

Open <http://localhost:8001>, finish initialization → scan the QR code to log in to WeChat Reading → add the public accounts you want to follow. To run only the data source: `cd deploy/we-mp-rss && docker compose up -d --build`.
You won't need that admin UI when the credential expires: the app's **Account** page can scan a new QR code (see [Running Modes](#running-modes)).
The collection channel is fixed to `weread_mp` (WeChat Reading web), which sidesteps the `ret=200013` risk-control block on the official platform backend API. The original WeWe RSS is archived and its closed-source relay went offline, hence we-mp-rss — for why we use a derived image and how to upgrade the patch, see [`deploy/we-mp-rss/README.md`](deploy/we-mp-rss/README.md) (Chinese).

**3. Configure and fetch**

Open `http://127.0.0.1:8765/?token=<the WX_WEB_TOKEN from step 1>` — **the token is mandatory**: without it you can only view pages, not call the API (this service can write to your disk and read your API key).

1. On the **Settings** page, enter your **DeepSeek API key** and **vault path**, then click "Test connectivity"
2. On the **Fetch Articles** page, start a fetch; run just 5 articles first if you like
3. Check the results on the **Articles** page; open the vault in Obsidian — the Graph View is your knowledge graph

## Running Modes

| Mode | Entry point | Notes |
|------|-------------|-------|
| Docker (recommended) | `docker compose up -d --build` | Data source + web UI, as above |
| CLI | `python process_articles.py` | One batch run, no long-running process |
| Web | `python process_articles.py web` | Browser UI — the same one as in the container |

For the source-code path, install dependencies first with `uv sync` (recommended) or `pip install -e .`.

**CLI**

```bash
python process_articles.py                   # process all unprocessed articles
python process_articles.py --limit 5         # process 5 articles only
python process_articles.py --force           # ignore the processed.json ledger and reprocess
python process_articles.py --article-id <id> # process one specific article ID
python process_articles.py delete "title-or-id"  # cascade-delete an article and all its derived data
./run.sh --limit 5                           # same as above, plus append output to logs/run.log
```

**Web**

```bash
python process_articles.py web                  # open the browser automatically
python process_articles.py web --port 8765 --no-open
# Binds 127.0.0.1 by default; to listen externally use the module entry point
# (the web subcommand only forwards --port / --no-open)
python -m wx_obsidian.web --host 0.0.0.0 --allow-remote
# equivalent env form: WX_WEB_HOST=0.0.0.0 python process_articles.py web
```

Eight pages: Dashboard / Fetch Articles / Latest / Articles / Settings / Public Accounts / Data Source Status / Account.
The Latest page lists articles from every subscribed account in reverse publish order — click a title to open the original, filter by account and load more pages, and articles that already have a note are tagged "in vault", while ones missing a body are tagged "fetching" / "pending" / "fetch failed" and can be retried; the Public Accounts page shows each account's sync and update time; the Account page shows the WeChat Reading login state and the data-source token, warns when the session is dead but bodies are served by the WeChat fallback, and lets you scan a QR code right there when the credential is truly gone, after which the app triggers a fetch for every subscribed account; the Articles page has search / filters / sorting / pagination / detail drawer / multi-select bulk delete / cascade delete / open in Obsidian; the Settings page has a native directory picker and an unsaved-changes guard; on list pages `/` focuses the search box.
Cross-cutting capabilities: a persistent **task dock** (switch pages without losing the stream), a **command palette** (`⌘K`), light and dark themes, filter & pagination state kept in the URL (survives refresh, back navigation and link sharing), proper empty / skeleton / error states, and automatic motion reduction under `prefers-reduced-motion`.
Things you must know:

- **Bound to `127.0.0.1` only; every `/api/*` except `/api/ping` requires the token printed at startup** (`X-WX-Token` header or `?token=`), so other machines on your LAN cannot reach it; progress is streamed over SSE and the client reconnects and replays events by sequence number after a disconnect or refresh
- **Secrets are only echoed as masks**: leaving a field empty on the Settings page means "don't change it" and won't wipe the stored key; logs go through the same masking filter (`sk-` / `token=` / `Bearer`)
- **Single-flight tasks**: only one fetch job at a time; a second request gets a 409 — `processed.json`, MOCs and concept pages must not be written concurrently
- **A successful QR login triggers fetching**: a fresh WeChat Reading cookie brings no new articles by itself (the upstream scheduler only refetches article bodies), so after the scan the app triggers a fetch for every subscribed account, 3 seconds apart to stay clear of rate limiting; progress shows in the bottom-right corner and you can keep using other pages
- **⚠️ Do not fetch from the CLI and the Web UI at the same time**: each holds its own job state and they will fight over `processed.json`. Pick one.
- **Shutting down**: "Quit" in the web UI (`POST /api/shutdown`) lets the service wrap up cleanly — in a container that is the "stop the service" entry point; Ctrl+C / SIGTERM in a terminal, or `pkill -f wx-obsidian`, work too

<p align="center">
  <img src="assets/web-run.png" alt="Fetch Articles page: article count presets, force-reprocess switch, live progress and stage timeline" width="820">
  <br><sub>Fetch Articles: article count and force-reprocess, live progress, stage timeline, failure list</sub>
</p>

<p align="center">
  <img src="assets/web-articles.png" alt="Articles page: search, category and status filters, detail drawer, multi-select bulk delete, open in Obsidian" width="820">
  <br><sub>Articles: search / filters / detail drawer / multi-select bulk delete / open in Obsidian</sub>
</p>

## Advanced Docker Deployment

| Topic | How | Why |
|-------|-----|-----|
| Local access only | `ports` defaults to `127.0.0.1:8765` | This service can write your vault and read your API key |
| Phone / other devices | `WX_WEB_BIND=0.0.0.0 docker compose up -d` | The token travels over plain HTTP on the wire; prefer Tailscale or an SSH tunnel |
| Change port | `WX_WEB_PORT=8766 docker compose up -d` | Run alongside another instance |
| Stable token | `WX_WEB_TOKEN` (`deploy/wx-obsidian/.env`) | The container restarts; a random token would invalidate bookmarked URLs |
| Where data lives | `~/.wx-obsidian` and the vault are **bind mounts** | `processed.json` must exist exactly once, otherwise the container and the host will each process the same article |
| Vault mount point | **Same absolute path** as on the host | Records store absolute paths; mounting at `/vault` makes cascade delete fail silently |
| Data source address | `http://we-mp-rss:8001` inside the container (injected by compose as `WX_SOURCE_BASE_URL`) | `config.json` is shared with the host; only environment-dependent values like the address differ per environment |
| Logs | `docker compose logs -f wx-obsidian` | There is no `~/Library/Logs` in a container; logs are also written to `~/.wx-obsidian/logs/app.log` |
| Restart policy | `on-failure` | So that "Quit" in the web UI really stops the service instead of Docker bringing it right back |

`WX_ALLOW_REMOTE=1` inside the container looks like a loosened security setting, but it isn't: connections arriving through the published port show up to the container as the **container gateway** (measured: `192.168.117.1`), and without it even the host's own browser gets a 403.
**The real boundary is the host port binding in the first row of the table; token validation is always enforced.**

> Migrating from the old setup (running `cd deploy/we-mp-rss && docker compose up -d` directly)? Run `cd deploy/we-mp-rss && docker compose down` first, otherwise container names collide. Data lives in mounted volumes and is unaffected.

### Native folder picker (optional)

The web backend runs on Linux inside the container, so it cannot raise the macOS folder panel. To use **Native panel** on the settings page, install the host picker once on the Mac:

```bash
./scripts/host-picker.sh install     # installs a LaunchAgent; starts at login
```

It listens on a random loopback port and writes its endpoint to `~/.wx-obsidian/host-picker.json` (0600). The container reaches it through `host.docker.internal`, and the panel still appears on this Mac; the chosen path is filled back into the page. Installation copies the modules it needs to `~/.wx-obsidian/host-picker/` (the repo may live under a macOS-protected directory such as `~/Downloads`, which a launchd process cannot read); re-run `install` after updating the repo. The first time you click **Native panel**, macOS asks whether the helper may control System Events — choose Allow and it will not ask again.

- Commands: `./scripts/host-picker.sh status`, `run` (foreground, for debugging), `uninstall`
- Without it, **Browse…** still lists the directories mounted into the container and the path can be saved the same way
- The container only sees directories mounted into it; picking an unmounted directory shows a warning instead of pretending the pick succeeded

## Configuration

| What | Where | Used by |
|------|-------|---------|
| API keys, runtime switches | `~/.wx-obsidian/.env` (template: [`.env.example`](.env.example)) | CLI / Web; the Web settings page writes it for you |
| Vault path, categories, data source, model providers, ports | `~/.wx-obsidian/config.json` | Shared by every entry point |
| Dedup ledger / similarity index / logs | `processed.json` / `similarity.sqlite` / `logs/` under `~/.wx-obsidian/` | Maintained automatically |

Common environment variables (key templates: [`.env.example`](.env.example); container switches: [`deploy/wx-obsidian/.env.example`](deploy/wx-obsidian/.env.example); `WX_SOURCE_BASE_URL` is injected by compose and not in any template):

| Variable | Required | Description |
|----------|----------|-------------|
| `DEEPSEEK_API_KEY` | Yes | LLM and Vision share this key by default |
| `DEEPSEEK_BASE_URL` / `MODEL_NAME` | No | Defaults: `https://api.deepseek.com` / `deepseek-flash` |
| `VISION_API_KEY` / `VISION_BASE_URL` / `VISION_MODEL_NAME` | No | Separate vision channel; when empty it shares the LLM one |
| `MAX_VISION_CONCURRENCY` / `VISION_TIMEOUT` / `VISION_MAX_RETRIES` | No | Defaults: `10` / `120` seconds / `2` |
| `WX_SOURCE` / `WX_SOURCE_BASE_URL` | No | Data source type (currently only `we_mp_rss`) and address override |
| `WX_WEB_TOKEN` / `WX_WEB_HOST` / `WX_ALLOW_REMOTE` | No | Web startup switches, used by container deployments |
| `DEBUG` | No | Boolean: `1/true/yes/on` enables, `0/false/no/off` (default) disables; unrecognized values are treated as off with a warning. When enabled, raw API responses are written to `last_response.txt` |

Key fields of `~/.wx-obsidian/config.json` (editable on the Web settings page):

```jsonc
{
  "source":   { "type": "we_mp_rss", "base_url": "http://localhost:8001", "timeout": 15, "token": "" },
  "fetch":    { "max_days": 7, "max_workers": 5, "feed_limit": 300 },
  "llm":      { "active": "deepseek", "providers": { "deepseek": { "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY" } }, "max_tokens": 4096, "temperature": 0.7 },
  "vision":   { "enabled": true, "active": "deepseek", "max_concurrency": 10, "timeout": 120, "max_tokens": 3072, "inline_images": true },
  "obsidian": { "vault_path": "/path/to/vault", "articles_dir": "公众号文章" },
  "web":      { "port": 8765, "host": "127.0.0.1", "allow_remote": false, "open_browser": true, "log_level": "INFO" }
}
```

- `web.host` / `web.allow_remote` are **startup-time** settings; restart after changing them (in a container, override with `WX_WEB_HOST` / `WX_ALLOW_REMOTE`)
- `llm.active` / `vision.active` point at any OpenAI-compatible provider under `providers`: add a provider to switch models without touching code
- `categories` is maintained by the program: the canonical list lives in `skills/classification/SKILL.md`; when a new category appears at runtime the program creates the directory / MOC and appends it to this list

## How It Works

```
process_articles.py / docker compose
    └── wx_obsidian/orchestrator.py           # 6-stage orchestrator, shared by CLI / Web
            ├── sources/     data acquisition (we-mp-rss client + article body extraction)
            ├── processing/  processing layer (Vision / LLM / Markdown / images / similarity)
            ├── output/      output layer (vault writes / validation / cascade delete)
            └── batch.py     parallel execution + exponential backoff retries
```

| Stage | Module | What it does |
|-------|--------|--------------|
| 1 Fetch | `orchestrator.py` | Fetch articles + extract the article body |
| 2 Vision | `processing/vision.py` | Semantic image descriptions (optional; falls back to plain text on failure) |
| 3 LLM | `processing/llm.py` | Generate structured JSON (category / summary / concepts / body) |
| 4 Markdown | `processing/markdown.py` + `output/validator.py` | Generate and validate Obsidian Markdown |
| 5 Image | `processing/images.py` | Replace `[IMG:N]` placeholders / insert images by keyword matching |
| 6 Write | `orchestrator.py` | Write to the vault + update `processed.json` |

Two more steps follow: computing article-to-article similarity (`processing/similarity.py`) and updating the MOC / concept pages / sub-directory splitting.
Vault layout: `_MOC.md` (master index) → per-category directories (`LLM基础/`, `Agent/` …) → `<sub_topic>/` sub-directories (a sub-directory is created and articles are migrated once a sub-topic reaches 3 articles), plus `Z归档/26/06/07.md` date archives and a `概念/` folder that aggregates every article referencing a concept.
Links come in three layers: article → concept (`[[concept name]]` extracted by the LLM), concept → article (backlinks aggregated automatically), article → article (a hybrid similarity algorithm, at most 3 related articles per note, threshold 0.05).

On the web side three flows talk to the data source directly: the Latest page reads the article list through `/api/source/articles` (read-only — no fetching, no vault writes, no API cost); after a successful QR login `web/feed_sync.py` triggers a fetch for every subscribed account one by one; and `web/source_sync.py` repeats the same trigger on the configured interval so the article library stays fresh while the login lasts. Each trigger makes the upstream start a collection thread, hence the gap between them, and all three call back into `web/content_backfill.py` when a round ends to backfill the articles still missing a body.

## Project Structure

```
wx-obsidian/
├── process_articles.py      # CLI entry (cli / web / delete / migrate-vault)
├── run.sh                   # convenience script for one manual run (also appends to logs/run.log)
├── wx_obsidian/
│   ├── orchestrator.py      # core orchestrator (6-stage pipeline, shared by CLI / Web)
│   ├── sources/             # data acquisition (we-mp-rss client + article body extraction)
│   ├── processing/          # processing layer (Vision / LLM / Markdown / images / similarity)
│   ├── output/              # output layer (vault writes / validation / cascade delete)
│   └── web/                 # FastAPI + Vue browser UI
├── skills/                  # 3 Skills that control note style
├── prompts/                 # LLM prompt templates (system / user split)
├── deploy/                  # Docker deployment recipes (incl. the derived we-mp-rss image)
├── tests/                   # unit tests + end-to-end smoke test
├── docs/                    # design specs and decision records
└── assets/                  # README screenshots
```

Detailed architecture, dependency flow, coding red lines (path safety / atomic writes / exception handling / layer isolation) and a "which file changes what" map live in [`AGENTS.md`](AGENTS.md) (Chinese).

| Document | Contents |
|----------|----------|
| [`AGENTS.md`](AGENTS.md) | Architecture, dependency flow, coding rules, change-impact map, task index (Chinese) |
| [`.github/CI.md`](.github/CI.md) | The CI / security-scan / CD pipelines and the secret-leak defenses (Chinese) |
| [`deploy/we-mp-rss/README.md`](deploy/we-mp-rss/README.md) | Why the data source uses a derived image, its release-time patch, and how to upgrade (Chinese) |
| [`docs/design-docs/`](docs/design-docs/) | Design specs and implementation decisions (Chinese) |

## Customizing the Output

Three Skill files control the AI output. Edit Markdown to change the style — no Python required:

| Skill | Path | Controls |
|-------|------|----------|
| `article-body` | `skills/article-body/SKILL.md` | Section structure (4-6 sections), writing style, table and image conventions |
| `classification` | `skills/classification/SKILL.md` | Category selection, sub_topic extraction rules |
| `note-metadata` | `skills/note-metadata/SKILL.md` | Frontmatter format, summary, concept extraction, related topics |

Prompts are split into system / user files (`string.Template` with `$variable` placeholders); edit the files to change prompts: `prompts/summarize_system.txt` (static, reusable by KV cache), `summarize_user.txt` (article body + Skill + image context injected), `refine_system.txt` / `refine_user.txt` (image refinement).

The single source of truth for the category list is `skills/classification/SKILL.md` (the Python side hard-codes no categories). Six groups are predefined; when a new category appears at runtime the program creates the directory / MOC and appends it to `categories` in `config.json`.
Predefined groups — model layer: LLM基础 (LLM basics), 训练与微调 (training & fine-tuning), 多模态与视觉 (multimodal & vision), 具身智能 (embodied AI); Agent layer: `Agent` (multi-agent collaboration, RAG, MCP, context engineering and prompt engineering all live here); application layer: AI编程 (AI coding), 后端技术 (backend engineering); engineering layer: Harness Engineering; ecosystem layer: AI产业生态 (AI ecosystem); fallback: 其他 (other).
When in conflict, "a specific topic wins over a foundational field": fine-tuning → 训练与微调, inference optimization → LLM基础.

> Skill / Prompt changes only affect articles processed afterwards; to regenerate existing articles with the new style use `python process_articles.py --force` (which consumes API quota again).

## FAQ

**The Account / Public Accounts / Latest pages show no data.**
`/feed/*.json` is a public read-only endpoint — **fetching requires no credentials**. Those three pages, however, read we-mp-rss's `/api/v1/wx/*` admin API, which requires a Bearer token.
Without one, the upstream returns 401: the Account and Public Accounts pages can only show "not found / not logged in", and the Latest page reports a missing or expired token. The token is a JWT and the Settings page shows its expiry; the command to obtain one is in [`deploy/we-mp-rss/README.md`](deploy/we-mp-rss/README.md) (Chinese).

**The WeChat Reading login has expired, so accounts stop updating.**
The Account page verifies the credential for real every time it opens — the QR dialog pops up only when the credential itself has expired, and you can click "Scan to log in again" at any time. After a successful scan the dialog closes, the app triggers a fetch for every subscribed account in the background, and once the progress card in the bottom-right corner finishes you can refresh the Public Accounts page to see the new update times. Use "Refresh QR code" when the code expires; if the scan still fails, "Open authorization page" takes you back to the we-mp-rss admin as a fallback.
No QR dialog appears for "token invalid" or "data source unreachable" states — fix the access token as described above, or confirm the data-source container is running.

**The Latest page shows "fetching" / "pending" / "fetch failed".**
These tags cover the three stages of body backfill: as soon as a collection round ends, the app hands every article still missing a body to the upstream, so freshly collected articles briefly show "fetching" or "pending" and turn normal within seconds to tens of seconds; ones that keep failing are tagged "fetch failed" with the number of attempts, and the "Retry" button on that row triggers another attempt immediately. Failures usually mean the article was deleted by its author, needs WeChat environment verification, or the data-source access token expired — retrying will not help the first two, but the original link still opens. If "backfill could not continue" appears at the top, fill in the access token as that message says.

**The Account page says the WeChat Reading session is dead.**
The cover API needs no session, while the content API does. Once the session expires the app can still discover new articles, but bodies can only come from the WeChat original page — hence a warning instead of "credential valid". Collection and body backfill keep working; click "Scan to log in again" if you want the more reliable content source back.

**Where are the logs?**
With Docker: `docker compose logs -f wx-obsidian` (also written to `~/.wx-obsidian/logs/app.log`). For source-code runs, logs go to `~/Library/Logs/wx-obsidian/app.log` on macOS and `~/.wx-obsidian/logs/app.log` on other platforms (2MB × 3 rotation).
Logs include pipeline progress printed via `print()` (in a container stdout is captured; for source runs `print()` still goes to the terminal), and `sk-` / `token=` / `Bearer` are always masked.
End-to-end self-check (temporary HOME + local stub upstream, runs a real cycle — **no API cost, no touching your real vault**):

```bash
.venv/bin/python tests/e2e/smoke_web.py            # fetch one article → assert note / ledger / logs / clean exit
.venv/bin/python tests/e2e/smoke_web.py --keep     # keep the temp directory for debugging
```

**I moved my vault.**
`file` entries in `processed.json` are absolute paths, so after moving you must migrate once; otherwise **cascade delete will fail silently** (dedup is unaffected, it keys on article ID):

```bash
python process_articles.py migrate-vault /Users/you/Documents --dry-run   # preview first
python process_articles.py migrate-vault /Users/you/Documents             # then execute
```

Obsidian registers vaults **by absolute path**, so re-open the moved folder via "Open folder as vault", otherwise "Open" reports Vault not found.

**Can I use a different model?**
Yes. Add an OpenAI-compatible provider under `llm.providers` / `vision.providers` in `config.json` (`base_url` + `model` + `api_key_env`), then point `llm.active` / `vision.active` at it; the Settings page can do the same. LLM and Vision can use different providers.

**Is there scheduled fetching?**
No. In-app scheduling has been removed entirely. Run `./run.sh [--limit N]`, `python process_articles.py`, or click fetch once on the Web "Fetch Articles" page.

**How do I delete an article that was already generated?** See the `delete` subcommand in the CLI section above: it removes the article `.md`, its entry in the category `_MOC.md`, related links in concept pages (deleting a concept page when no links remain), the date-archive entry and the `processed.json` record.

## Development

Tech stack: Python 3.12 · FastAPI · requests / mistune / jieba · SQLite FTS5; frontend Vue 3 + TypeScript + Tailwind v4 + Reka UI + Phosphor; deployment via Docker Compose.

```bash
ruff check .                                                # lint
ruff format .                                               # format
mypy wx_obsidian/ process_articles.py validate_markdown.py   # type-check
pytest                                                      # unit tests
python validate_markdown.py --dir <directory>                # validate Markdown files
```

`tests/conftest.py` redirects the real state files under `~/.wx-obsidian/` to a temporary directory, so running tests never touches your `processed.json`.

Frontend (`wx_obsidian/web/frontend`, a bespoke design system: Tailwind v4 + Reka UI + Phosphor): after `pnpm install` run `pnpm verify` (= `type-check && style-guard && test && build` — all four gates are required); for development use `pnpm dev` (port 5173, `/api` proxied to 8765).

Issues and PRs are welcome. Please make sure the checks above pass before submitting, and keep [README.md](README.md) (Chinese) and this English version in sync when editing docs. Release notes live in [GitHub Releases](https://github.com/hxxxi-malog/wx-obsidian/releases).

The three pipelines (CI / security scan / CD) and the three-layer defense for `.env` secrets are documented in [`.github/CI.md`](.github/CI.md) (Chinese).

## Disclaimer

Article bodies, images and the notes being generated are sent to the LLM API you configure (DeepSeek by default) for generation and refinement.
The data source, your vault, `processed.json` and the similarity index stay on your own machine; this project uploads nothing to any other third party.
Please make sure you have the rights to use the content you fetch.

## Acknowledgements

The data source and WeChat article-body extraction rely on [we-mp-rss](https://github.com/rachelos/we-mp-rss) (deployment recipe under `deploy/we-mp-rss/`); the web UI is built with FastAPI + Vue 3 + Tailwind.

## License

[MIT](LICENSE) © malog
