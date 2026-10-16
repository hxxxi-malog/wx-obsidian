# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

wx-obsidian is a WeChat public account article → Obsidian knowledge base automation pipeline. It fetches articles from a we-mp-rss instance, calls the DeepSeek API to generate structured notes, and writes them into an Obsidian vault with bidirectional links and a knowledge graph.

## Commands

```bash
# Run the main pipeline (processes all unprocessed articles)
python process_articles.py

# Process limited articles
python process_articles.py --limit 5

# Web mode (browser UI; prints a token-bearing URL)
python process_articles.py web

# Bring up everything (we-mp-rss + wx-obsidian web) — the primary way to run it
docker compose up -d --build
docker compose logs -f wx-obsidian

# End-to-end smoke test (temp HOME + stub upstream; no API cost, no real vault)
.venv/bin/python tests/e2e/smoke_web.py

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

# 前端（自建设计系统：Tailwind v4 + Reka UI + Phosphor）
cd wx_obsidian/web/frontend
pnpm style-guard && pnpm type-check && pnpm test && pnpm build   # 四道门，缺一不可
pnpm dev                                                        # 5173，/api 代理到 8765
node scripts/ui-smoke.mjs --base http://127.0.0.1:8765 --token "$WX_WEB_TOKEN" --out /tmp/shots
```

## Architecture

Python package `wx_obsidian/` with three layers + CLI/Web entries:

```
wx_obsidian/
├── config.py              ← 底层配置/持久化（.env, processed.json, skills 加载）
├── config_manager.py      ← 用户级配置（~/.wx-obsidian/config.json, 连通性测试）
├── models.py              ← 全局数据模型（ProcessingResult, HealthStatus, Feed 等）
├── orchestrator.py        ← 核心编排器（6-stage pipeline，CLI/Web 共享）
├── batch.py               ← 批量并行处理 + 指数退避重试
├── sources/               ← 数据获取层
│   ├── base.py            ← ArticleSource 协议（编排层依赖的抽象）
│   ├── factory.py         ← 数据源工厂（按 WX_SOURCE / source.type 选择）
│   ├── we_mp_rss.py       ← we-mp-rss API 客户端（当前唯一数据源）
│   └── rss.py             ← 取文契约 + 微信文章正文提取
├── processing/            ← 处理层
│   ├── pipeline.py        ← Pipeline 引擎（函数组合式 stage）
│   ├── vision.py          ← 多模态 Vision API（广告过滤 + 并发调用）
│   ├── llm.py             ← DeepSeek API 调用 + prompt 构建 + 响应解析
│   ├── images.py          ← 图片提取 + 语义匹配 + 自动插入
│   ├── markdown.py        ← Markdown 生成（frontmatter + body + 假图片清除）
│   └── similarity.py      ← 文章相似度（SQLite FTS5/BM25F + jieba + RRF）
├── output/                ← 输出层
│   ├── vault.py           ← Obsidian Vault 操作（MOC、概念页面、分类、子目录）
│   ├── validator.py       ← Markdown 格式校验与自动修复
│   └── cleanup.py         ← 文章级联删除（MOC、概念、归档、processed.json）
├── web/                   ← Web 层（FastAPI + Vue），与 cli.py 平级
├── vault_migration.py     ← 换 Vault 路径时修正配置与 processed.json
└── cli.py                 ← CLI 入口，调用 Orchestrator
```

顶层入口：
- `process_articles.py` — 薄 CLI 入口，调用 `wx_obsidian.cli.main()`；子命令：`cli`（默认）/ `web` / `delete` / `migrate-vault`
- `validate_markdown.py` — 向后兼容入口，转发到 `wx_obsidian.output.validator`

### Dependency Flow

```
cli.py / web/runtime.py
    └── orchestrator.py        ← 6-stage pipeline
            ├── sources/*          （数据获取）
            ├── processing/*       （处理层）
            ├── output/*           （输出层）
            └── batch.py           （并行执行 + 指数退避重试）
```

- `config.py` 是最底层，被所有模块依赖
- `config_manager.py` 提供用户级配置，被 orchestrator / web 依赖
- `sources/`, `processing/`, `output/` 之间不互相依赖（通过 orchestrator 编排）
- `processing/images.py` 和 `processing/markdown.py` 是纯函数模块

### Skill System

3 Skill files in `skills/` control the AI output style without code changes:

| Skill | Path | Controls |
|-------|------|----------|
| `article-body` | `skills/article-body/SKILL.md` | Chapter structure (4-6 sections), writing style, table usage |
| `classification` | `skills/classification/SKILL.md` | category selection (6 groups / 15 categories), sub_topic extraction rules |
| `note-metadata` | `skills/note-metadata/SKILL.md` | Frontmatter format, summary, concept extraction, related topics |

### Prompt Template

Prompt 拆成 system / user 两份（`string.Template` + `$variable` 占位符），改文件即可换 prompt，无需动 Python：

| 文件 | 用途 |
|------|------|
| `prompts/summarize_system.txt` | 总结 system prompt（静态，所有文章共享，可被 KV cache 复用） |
| `prompts/summarize_user.txt` | 总结 user prompt（注入正文 + Skill + 图片上下文） |
| `prompts/refine_system.txt` | 图片修订 system prompt |
| `prompts/refine_user.txt` | 图片修订 user prompt |

### Data Flow

```
we-mp-rss (localhost:8001) → fetch_articles(source=...) → 文章列表
    → summarize_article() → DeepSeek API + 3 Skills → structured JSON
    → generate_markdown() → Obsidian .md file
    → remove_non_cdn_images() + insert_images_into_markdown() → 图片自动插入
    → validate_and_fix() → 格式校验与修复
    → write file + update MOC + create concept pages + check sub-directory splitting
```

### Key State Files

- **`processed.json`** — Tracks processed article IDs to avoid reprocessing. Atomic saves after each article.
- **`~/.wx-obsidian/config.json`** — ConfigManager 管理的用户级配置（vault 路径、分类、数据源、模型渠道）。New categories appended at runtime.
- **`~/.wx-obsidian/.env`** — API keys 与运行时开关（不入库）。

## Configuration

- **`.env`** — API keys and runtime config (not committed). Copy from `.env.example`.
- **`~/.wx-obsidian/config.json`** — Vault path (`obsidian.vault_path`), articles directory name, category list, data source, model channels.
- **`deploy/we-mp-rss/`** — Deploys the we-mp-rss data source (patched image, loopback-only port). See its own README for the publish-time patch rationale.

## Rules（编码红线）

以下规则不可违反，改完代码必须验证：

1. **验证闭环**：代码变更必须通过 `ruff check` + `mypy`，不允许用 `# type: ignore` 或禁用 ruff 规则来绕过
2. **路径安全**：LLM 返回的 category / sub_topic 必须经过 `re.sub(r'[<>:"/\\|?*]', "_", ...)` 过滤后才能用于文件路径
3. **原子写入**：持久化文件（processed.json）必须用 tempfile + os.replace 模式，防止中断导致数据损坏
4. **异常规范**：禁止裸 `except:`，必须指定异常类型；外部 API 调用必须有 timeout
5. **配置外置**：禁止硬编码路径、密钥、公众号名称，必须走 ~/.wx-obsidian/config.json 或 .env
6. **依赖隔离**：sources/、processing/、output/ 之间不互相依赖，通过 orchestrator.py 编排

## Dev-Map（开发导航地图）

### 文件 → 职责 → 改动影响

| 文件 | 职责 | 改动时需关注 |
|------|------|-------------|
| `config.py` | 配置/持久化 | 被所有模块依赖，改动影响全局 |
| `config_manager.py` | 用户级配置 + 连通性测试 | 渠道解析（`resolve_llm_channel`）变更影响 llm/vision |
| `orchestrator.py` | 流程编排（6-stage pipeline） | 串联所有模块，改动需验证完整流程 |
| `sources/rss.py` | RSS 抓取 + 文章正文提取 | 返回值格式变更影响 orchestrator.py |
| `processing/pipeline.py` | Pipeline 引擎（stage 组合） | 改 stage 协议影响 orchestrator 的 6 个 stage |
| `processing/vision.py` | 多模态 Vision API | 失败必须降级为纯文本，不能中断管线 |
| `processing/llm.py` | DeepSeek API 调用 + prompt 构建 | 改动 prompt 模板或响应解析影响 markdown.py |
| `processing/images.py` | 图片提取与插入 | 纯函数，改动影响 orchestrator.py 中的调用 |
| `processing/markdown.py` | Markdown 生成 + 假图片清除 | 纯函数，输出格式影响 validator |
| `processing/similarity.py` | 文章相似度（FTS5 + RRF） | 改权重/阈值影响已有关联的推荐质量 |
| `output/vault.py` | Obsidian Vault 操作 | 文件系统操作，改动影响 orchestrator.py |
| `output/validator.py` | Markdown 格式校验与修复 | 校验规则变更可能影响已生成的文件 |
| `cli.py` | CLI 入口 | 薄封装，只负责参数解析与调用 Orchestrator |
| `web/api.py` | Web 接口 + 安全中间件 | 端点签名历史上避开 `X \| None`（py3.9 限制，3.12 起已解除） |
| `web/jobs.py` | 任务单飞 + SSE 事件 | `on_progress` 在工作线程调用，状态只能在事件循环线程内改 |
| `web/articles.py` | 文章列表/详情/级联删除 | `sort`/`order` 是白名单参数（非法值回落默认顺序），排序必须在分页切片前 |
| `web/frontend/` | Vue 界面（自建设计系统） | 改动后跑 `pnpm style-guard && pnpm type-check && pnpm test && pnpm build`；产物由 FastAPI 同源托管 |
| `web/frontend/src/styles/tokens.css` | 设计 token（三层 + 深浅两套） | 改 token 影响全站观感；业务代码只允许语义类，禁裸色值 |
| `web/frontend/src/styles/veil.css` | 粘性条渐进模糊遮罩（顶栏 / 保存条） | 层数必须 = `lib/scroll.ts` 的 `VEIL_BANDS` = CSS `:nth-child` 规则数；`opacity` 只能落叶子层，否则容器变 backdrop root、模糊失效（spec §3.7） |
| `web/frontend/src/lib/api/endpoints.ts` | 所有接口路径的唯一事实来源 | 加接口要同步 `tests/test_web/test_frontend_contract.py` 的必选清单 |
| `web/frontend/src/components/ui/` | 设计系统原子组件（冻结契约） | 改 props 会波及全部页面；新增能力先看 spec §5.3 |
| `web/frontend/scripts/{style-guard,ui-smoke}.mjs` | 风格守卫 + 浏览器冒烟 | ui-smoke 需要本机 Chrome；无 token 时断言「未授权引导」而不是白屏 |
| `web/logging_setup.py` | 日志落盘 + 密钥掩码 | 容器/无终端部署唯一的排错通道；掩码过滤器不能漏 |
| `web/logs.py` | 日志环形缓冲（页面告警面板） | 只收 WARNING+，与文件日志互不影响 |
| `web/__main__.py` | 启动器（端口/单实例/退出/日志） | 改判定逻辑要同步 `tests/test_web/test_launcher.py` |
| `tests/e2e/smoke_web.py` | 端到端冒烟（临时 HOME + stub 上游） | 改启动器 / 日志 / 抓取链路后跑一次；断言笔记落盘、账本写入、日志掩码、干净退出 |
| `docker-compose.yml` | 一条命令拉起全部服务 | `include` 引入 we-mp-rss 配方；`ports` 默认只绑 127.0.0.1 |
| `deploy/wx-obsidian/Dockerfile` | Web 服务镜像 | 多阶段构建前端；改依赖只改 pyproject（单一来源） |
| `config.py` 的 `resolve_source_base_url` | 数据源地址解析（env 优先） | 适配器/探活/CLI/Web 必须共用，否则容器里健康探活会连错地址 |
| `vault_migration.py` | Vault 路径迁移 | 影响 processed.json 的 `file` 字段与级联删除 |
| `prompts/summarize_{system,user}.txt`, `prompts/refine_{system,user}.txt` | Prompt 模板 | 改动直接影响 LLM 输出质量 |
| `skills/*/SKILL.md` | LLM 输出风格控制 | 改动影响所有新生成的笔记 |

### 常见开发任务 → 涉及文件

| 任务 | 涉及文件 |
|------|---------|
| 调整笔记输出格式 | `skills/article-body/SKILL.md`, `skills/note-metadata/SKILL.md` |
| 修改分类体系 | `skills/classification/SKILL.md`, `~/.wx-obsidian/config.json` |
| 优化 LLM 提示词 | `prompts/summarize_{system,user}.txt`, `processing/llm.py` |
| 修复格式校验问题 | `output/validator.py` |
| 调整图片插入逻辑 | `processing/images.py` |
| 修改 Vault 目录结构 | `output/vault.py`, `~/.wx-obsidian/config.json` |
| 新增数据源 | `sources/` 下新建模块 + 实现 ArticleSource, `sources/factory.py`, `orchestrator.py` |
| 文章级联删除 | `output/cleanup.py`, `web/articles.py`, `process_articles.py` |
| 修改 Web 界面 | `web/frontend/src/views/`、`src/components/{ui,domain,layout}/`，然后跑前端四道门 + `pnpm build` |
| 新增 Web 接口 | `web/api.py`、`web/frontend/src/lib/api/{endpoints,types}.ts`、`tests/test_web/test_frontend_contract.py` |
| 调整 Web 启动器行为（端口/单实例/退出） | `web/__main__.py`, `web/logging_setup.py`, `tests/test_web/test_launcher.py` |
| 端到端自检 | `tests/e2e/smoke_web.py`（临时 HOME + stub 上游，真跑一轮并断言笔记/账本/日志/退出） |
| 调整容器部署 | `docker-compose.yml`, `deploy/wx-obsidian/Dockerfile`, `deploy/wx-obsidian/.env.example` |
| 改动 Web 启动期行为 | `web/__main__.py`（`--host`/`WX_WEB_HOST`、`WX_WEB_TOKEN`、`WX_ALLOW_REMOTE`） |

### 关键常量速查

| 常量 | 位置 | 值 | 说明 |
|------|------|---|------|
| `MAX_PROMPT_CONTENT` | config.py | 100000 | 发给 LLM 的最大字符数 |
| `MAX_ARTICLE_LENGTH` | config.py | 100000 | 文章最大长度 |
| `SUB_TOPIC_THRESHOLD` | config.py | 3 | 同主题几篇后创建子目录 |

## Development Notes

- Python 3.12+ required (uses `from __future__ import annotations` for type hints；
  `requires-python` / `[tool.mypy] python_version` / CI / 运行时镜像四者都钉在 3.12).
- FastAPI 端点签名里 `X | None` 的历史禁令已解除：那是 py3.9 的限制（运行时求值注解 + 无 PEP 604），底线抬到 3.12 后新端点可以正常用。现有签名保持原样；可省 body 仍用 `Body(default_factory=dict)` 表达。
- Ruff config: line-length 100, target py312, E501 ignored (f-string prompt templates can't be split).
- `MAX_PROMPT_CONTENT = 100000` and `MAX_ARTICLE_LENGTH = 100000` cap content sent to the API.
- Path safety: LLM-returned category/sub_topic names are sanitized with `re.sub(r'[<>:"/\\|?*]', "_", ...)`.
- Images are inserted programmatically (not by LLM) via keyword-overlap matching between image context and markdown sections.
- `run.sh` is self-contained: config and keys come from `~/.wx-obsidian/` (`config.json` + `.env`).
- **CLI 与 Web 不能同时抓取**：两边各自持有任务状态，会争抢 `processed.json`/MOC 的写入。Web 服务（或容器）跑着时不要在 CLI 里发起抓取（彻底解法见 tasks.md P4 的跨进程锁）。
- Web 启动器读 `web.port` / `web.open_browser` / `web.log_level`（`_DEFAULT_CONFIG` 已补）；端口被占时先 `GET /api/ping` 判断是自己人（只开浏览器后退出）还是别人（立刻报错，不等超时）。
- `create_app(on_quit=...)` 是「退出应用」的注入点 → `POST /api/shutdown`。容器里没有别的停法（`docker stop` 是硬杀），所以界面上的退出入口是优雅收尾的主路径；省略 `on_quit` 时该接口如实返回 501。
- 日志必须落盘：源码方式在 macOS 上落 `~/Library/Logs/wx-obsidian/app.log`，其它平台落 `~/.wx-obsidian/logs/app.log`（2MB×3 轮转）；容器、systemd、nohup 下都没有终端，启动期失败只能靠日志回溯。日志统一过 `MaskSecretsFilter`（`sk-` / `token=` / `Bearer`），写日志失败只降级不拦启动。
- 打包以 **`requires-python = ">=3.12"`** 为准（与运行时镜像同档），所以 dev 依赖的 mypy 上界已放开为 `mypy>=1.10`：mypy 2.x 需要 >=3.10 且移除了 3.9 目标，而我们现在**就是** 3.12 —— 锁文件里实测升到 2.4.0 且 37 个源文件全绿。
- 端到端验证不看「能不能启动」，看 `tests/e2e/smoke_web.py`：独立 HOME、临时 cwd、本地 stub 上游真跑一轮，断言笔记落盘 + processed.json/config.json 写入 + 日志掩码 + `POST /api/shutdown` 干净退出。
- **主用运行方式是 Docker**（`docker compose up -d --build`，见 README 的 Docker 章节）：数据源与 Web 服务一个 compose 项目，`include` 复用 `deploy/we-mp-rss` 的配方，默认只绑 `127.0.0.1`。
- 容器里有两处必须注意：①**vault 必须挂到与宿主机相同的绝对路径**（`processed.json` 存的是绝对路径，挂 `/vault` 会让级联删除静默失效、记录分叉）；②**必须开 `WX_ALLOW_REMOTE=1`**，因为经发布端口进来的连接，容器看到的客户端 IP 是容器网关（实测 `192.168.117.1`），不放行连宿主浏览器都 403；真正的边界是宿主端口绑定。
- 数据源地址走 `config.resolve_source_base_url()`（`WX_SOURCE_BASE_URL` 优先），容器用服务名、宿主用 `localhost`，而 `config.json` 只有一份。**新增任何读 `source.base_url` 的代码都必须走这个函数**，否则会出现「抓取正常但探活失败」这类只在一侧暴露的问题。
- 容器构建的两个坑：`package.json` 里的 `pnpm.onlyBuiltDependencies` 在 pnpm 12 已失效（10.13+ 移到 `pnpm-workspace.yaml`），`pnpm install --frozen-lockfile` 会直接报错退出，故 Dockerfile 显式带 `--config.strict-dep-builds=false`；`COPY` 会保留源文件权限，而本机检出的文件可能是 0600（git 只记可执行位），必须 `chown -R wx:wx /app`，否则非 root 启动即 `PermissionError`。
- 构建期默认走国内镜像（npm→npmmirror、pip→腾讯云），可用 `--build-arg NPM_REGISTRY=... / PIP_INDEX_URL=...` 覆盖：实测直连 npmjs 3.1s 且会 ECONNRESET。
- 没有系统级定时任务，应用内调度（历史上有过 TUI 的 Scheduler 与 Web 的定时任务页）也已整体移除：手动跑 `./run.sh [--limit N]`、`python process_articles.py`，或在 Web 的「文章抓取」页点一次。`_DEFAULT_CONFIG` 里不再有 `scheduler` 段，加载时会自动剔除历史遗留的该配置段。
- Model channels are switchable through `llm.active` / `vision.active` + `providers`; both default to `deepseek-flash` (supports image input).

## Harness 开发范式

基于 Harness Engineering 理念，在 `.agents/skills/`（Claude Code 侧同名的镜像在 `.claude/skills/`）下建立了三层开发约束体系：

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
