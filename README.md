# wx-obsidian

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/DeepSeek_API-Compatible-orange" alt="DeepSeek">
  <img src="https://img.shields.io/badge/DeepSeek_Vision-Multimodal-red" alt="DeepSeek Vision">
  <img src="https://img.shields.io/badge/we--mp--rss-Docker-green" alt="we-mp-rss">
  <img src="https://img.shields.io/badge/Obsidian-Compatible-purple" alt="Obsidian">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

微信公众号文章 -> Obsidian 知识库自动化处理器。自动抓取公众号文章，通过 LLM 生成结构化笔记，写入 Obsidian Vault 并维护双向链接知识图谱。

## 核心特性

- **CLI + TUI 双模式**：命令行批量处理 + [Textual](https://textual.textualize.io/) 终端图形界面
- **增量抓取**：通过 [we-mp-rss](https://github.com/rachelos/we-mp-rss) 拉取公众号文章，按 `processed.json` 去重，不重复处理
- **批量并行处理**：ThreadPoolExecutor 并行处理多篇文章，指数退避重试（最多 3 次，覆盖 429/5xx/网络错误）
- **多模态 Vision**：DeepSeek 视觉模型（`deepseek-flash`）识别文章图片，为 LLM 提供语义描述，无 Vision 时自动降级到纯文本 + 关键词匹配
- **3-Skill 系统**：模块化控制输出风格，写作风格、分类规则、元数据格式独立配置，改 Skill 即改笔记风格
- **智能分类**：15 个预设分类 + 动态新增，同一子主题积累 3 篇后自动创建子目录
- **知识图谱**：`[[双向链接]]` 关联已有文章和概念页面，Obsidian Graph View 自动成图
- **混合关联**：SQLite FTS5 全文检索 + jieba 分词 + 概念模糊匹配 + 标签 Jaccard，自动计算文章间相似度并持久化索引
- **级联删除**：一键删除文章及其所有关联数据（MOC 条目、概念页面、归档、processed.json）
- **格式保障**：自动校验修复 + mistune 结构性检测 + LLM 反馈修正，三层格式保障
- **应用内调度（可选）**：APScheduler 集成，TUI 运行时按 cron 抓取与处理；不跑 TUI 时用 `./run.sh` 手动触发
- **可切换模型渠道**：`llm.active` / `vision.active` 指向任意 OpenAI 兼容渠道，加一个 provider 即可换模型，无需改代码

## Quick Start

### 前置条件

- Python 3.9+
- Docker（用于部署 [we-mp-rss](https://github.com/rachelos/we-mp-rss) 数据源）
- [DeepSeek API Key](https://platform.deepseek.com/)（必填；语言模型与视觉模型默认共用，`deepseek-flash` 支持图像输入）

### 1. 安装

```bash
git clone https://github.com/yourname/wx-obsidian.git
cd wx-obsidian

# uv（推荐）
uv sync

# 或 pip
pip install -e .
```

### 2. 启动 we-mp-rss 数据源

原 WeWe RSS 已归档、其闭源中转下线，本项目改用 [we-mp-rss](https://github.com/rachelos/we-mp-rss)。
部署配置见 `docker-compose.yml`：采集通道固定为 `weread_mp`（微信读书 Web），
绕开公众平台后台接口的 `ret=200013` 风控；并附带修复「发布时间 = 抓取时间」的派生镜像。

```bash
cd deploy/we-mp-rss
cp .env.example .env          # 填写 WERSS_PASS / WEREAD_COOKIE
docker compose up -d --build
```

访问 `http://localhost:8001` 完成初始化并登录微信读书，然后添加要订阅的公众号。
细节（为什么用派生镜像、如何升级上游补丁）见 [`deploy/we-mp-rss/README.md`](deploy/we-mp-rss/README.md)。

### 3. 配置

**方式一：TUI 界面配置（推荐）**

```bash
python process_articles.py tui
```

进入 Config 界面，填写配置并点击「测试连通性」确认：

| 配置项 | 必填 | 说明 |
|--------|------|------|
| DeepSeek API Key | 是 | [获取地址](https://platform.deepseek.com/) |
| Vision API Key | 否 | 独立视觉渠道 Key；留空则与 DeepSeek Key 共用 |
| 知识库路径 | 是 | Obsidian Vault 路径（支持目录选择器） |
| we-mp-rss 服务地址 | 否 | 默认 `http://localhost:8001` |

**方式二：手动编辑配置文件**

```bash
cp .env.example ~/.wx-obsidian/.env
# 编辑 ~/.wx-obsidian/.env 填写 API Key
# 编辑 ~/.wx-obsidian/config.json 设置 obsidian.vault_path 等
```

### 4. 抓取并处理文章

```bash
# 处理所有未处理的文章
python process_articles.py

# 只处理 5 篇（试跑）
python process_articles.py --limit 5

# 或者用封装脚本：输出同时追加到 logs/run.log
./run.sh
./run.sh --limit 5
```

> 本项目**没有系统级定时任务**（原 macOS LaunchAgent 因 TCC 无法读取 `~/Downloads`，41 次全部失败，已移除）。
> 需要自动化就用 TUI 的 Scheduler 界面，它只在应用运行期间生效。

### 5. 查看结果

打开 Obsidian，进入配置的 Vault，找到 `公众号文章/` 目录。每篇文章生成一个 `.md` 文件，包含 frontmatter、结构化正文、`[[双向链接]]` 和图片。Graph View 中可以看到知识图谱。

## 使用方式

### CLI 模式

```bash
# 处理所有未处理的文章
python process_articles.py

# 限制处理数量
python process_articles.py --limit 5
```

### TUI 模式

```bash
python process_articles.py tui
```

| 界面 | 功能 |
|------|------|
| Home | 状态概览、快速操作入口 |
| Container | we-mp-rss 服务状态监控、启停控制 |
| Account | 微信登录状态、扫码保活 |
| Feeds | 公众号订阅管理 |
| Config | LLM / Vision / Obsidian 配置编辑、连通性测试 |
| Fetch | 手动触发文章抓取与处理 |
| Scheduler | 应用内定时任务（cron 表达式；需保持 TUI 运行） |

### Web 模式（浏览器界面）

```bash
python process_articles.py web            # 自动打开浏览器
python process_articles.py web --port 8765 --no-open
```

启动后终端会打印带 token 的地址；**这个 token 是必须的**，直接访问 `127.0.0.1:8765` 只能看到页面、调不通接口（服务能写你的硬盘、读你的 API Key，所以不能裸奔）。

| 页面 | 功能 |
|------|------|
| 状态总览 | 数据源 / LLM / Vision / Vault 四项健康状态、处理统计、分类分布、首启引导与环境告警 |
| 文章抓取 | 发起抓取（篇数、强制重跑）、实时进度与阶段、逐篇结果、失败清单、系统告警、中途取消、重试失败文章 |
| 文章管理 | 搜索 / 分类 / 状态筛选、分页、双击在 Obsidian 打开、级联删除 |
| 配置管理 | LLM / Vision / 数据源 / Vault 路径 / 抓取参数 / Web 端口、连通性测试、**原生目录选择器**、退出应用 |
| 公众号管理 | 订阅列表、按链接添加订阅、取消订阅 |
| 数据源状态 | we-mp-rss 可达性、上游返回详情 |
| 账号管理 | 登录状态、扫码登录地址、刷新 Cookie |
| 定时任务 | 间隔抓取（1/2/6/12/24h）与保活任务、立即执行、删除；**设定写进 config.json，重启自动恢复** |

几个要点：

- **只绑 `127.0.0.1`**，且所有 `/api/*` 都需要启动时打印的 token（`X-WX-Token` 头或 `?token=`）。局域网内其它机器访问不到。
- **密钥只回显掩码**：配置页留空表示「不修改」，不会把已存的 Key 清掉。日志同样过掩码过滤器（`sk-`、`token=`、`Bearer`）。
- **任务单飞**：同一时刻只允许一个抓取任务，第二个请求返回 409——`processed.json`、MOC、概念页都不能并发写。
- **调度器常驻**：定时任务跑在 Web 服务进程里，浏览器关掉也照常执行（TUI 时代的定时任务需要 TUI 一直开着）。
- **退出请用页面上的「退出应用」**（配置管理页底部）。窗口化的 `.app` 没有 Cocoa 事件循环，系统菜单的 Cmd+Q 到不了它。
- ⚠️ **不要同时用 TUI 和 Web 发起抓取**：两者各自持有任务状态，会争抢 `processed.json` 的写入。要跑就开一个。
- 进度走 SSE；断线或刷新页面会自动重连并按事件序号补齐，不会丢中间的进度。

### 桌面应用（macOS .app）

```bash
./packaging/build_app.sh              # 构建 + ad-hoc 签名 + 资源自检
./packaging/build_app.sh --dmg        # 顺带产出 dist/wx-obsidian.dmg（自用可选）
cp -R dist/wx-obsidian.app /Applications/
```

双击即可：读配置里的端口起本地服务 → 自动打开浏览器 → 常驻。要点：

- 用**独立的 Python 3.12 构建 venv**（开发 venv 是 3.9，已 EOL），PyInstaller `onedir` 模式，冷启动秒开；图标由 `packaging/make_icon.py` 生成（无设计稿但至少不是一张白纸）。
- 自用只需 ad-hoc 签名（脚本已做并校验）；**要分发给别人**得 Developer ID + 公证，否则对方双击会报「已损坏」。
- **不要从 `~/Downloads` 运行**，装到 `/Applications`。macOS 的 TCC 会拦截 `~/Downloads` 下的读写（本项目早期的 LaunchAgent 就是这么废掉的）；vault 若在 `~/Downloads`，状态总览页会直接提示迁移。
- 冻结包不读仓库里的 `.env`（密钥不入包），配置与密钥只认 `~/.wx-obsidian/`。
- `.app` 里不含 TUI（`--exclude-module textual`），TUI 仍可用源码方式运行。
- **端口被占**：先探测是不是自己人——是就只开浏览器（复用 `~/.wx-obsidian/web.token`），不是就立刻报错，不会傻等超时。
- **退出**：用网页「配置管理 → 退出应用」（`POST /api/shutdown`），或 `pkill -f wx-obsidian`；终端里 Ctrl+C / SIGTERM 同样有效。

### 排错：日志在哪

`.app` 是 `console=False` 打包的，没有终端——所以日志必须落盘：

```bash
tail -f ~/Library/Logs/wx-obsidian/app.log     # 2MB × 3 轮转
```

日志里包含 `print()` 出来的管线进度（冻结包会自动接管 stdout/stderr），并且 `sk-`、`token=`、`Bearer` 都会被掩码 —— 排错通道本身不能变成泄漏面。启动阶段失败（端口冲突、配置损坏）除了写日志还会弹一个系统对话框，不会静默什么都不发生。

冻结包的端到端自检（**不花 API 费用、不碰真实 vault**，用临时 HOME + 本地 stub 上游真跑一轮抓取并断言笔记落盘）：

```bash
.venv/bin/python packaging/smoke_frozen.py            # 用 dist/wx-obsidian.app
.venv/bin/python packaging/smoke_frozen.py --keep     # 保留临时目录便于排查
```

### 换过 Vault 路径之后

`processed.json` 里记的 `file` 是绝对路径。搬动 Vault 后必须跑一次迁移，否则**级联删除会静默失效**（去重不受影响，因为去重按文章 ID）：

```bash
python process_articles.py migrate-vault /Users/you/Documents --dry-run   # 先预览
python process_articles.py migrate-vault /Users/you/Documents             # 再执行
```

另外 Obsidian 是**按绝对路径**注册仓库的，搬完还要在 Obsidian 里用「打开文件夹作为仓库」重新选一次，否则点「打开」会报 Vault not found。

### 级联删除

```bash
python process_articles.py delete "文章标题或ID"
```

清理范围：文章 `.md` 文件、分类 `_MOC.md` 中的条目、概念页面中的相关链接（无剩余链接时删除概念页面）、日归档文件中的条目、`processed.json` 中的记录。

## 架构

### 整体流程

```
process_articles.py
    ├── CLI 模式 -> wx_obsidian/cli.py -> Orchestrator
    ├── TUI 模式 -> wx_obsidian/tui/   -> Orchestrator
    └── Web 模式 -> wx_obsidian/web/   -> Orchestrator

Orchestrator（核心编排器）
    │
    ├─ Stage 1: Fetch       <- we-mp-rss 拉取文章 + 正文提取
    ├─ Stage 2: Vision      <- DeepSeek 视觉模型识别图片（可选，失败自动降级）
    ├─ Stage 3: LLM         <- DeepSeek 生成结构化笔记（有 Vision 时含图片上下文）
    ├─ Stage 4: Markdown     <- 生成 Obsidian Markdown + 三层格式校验
    ├─ Stage 5: Image        <- 替换 [IMG:N] 占位符 / 关键词匹配插入图片
    ├─ Stage 6: Write        <- 写入 Vault 文件
    ├─ Related               <- 混合关联算法计算文章间相似度（SQLite FTS5 + jieba）
    └─ Knowledge Graph       <- 更新 MOC、概念页面、子目录拆分
```

### 6-Stage Pipeline

每篇文章经过 6 个 stage 串行处理，基于函数组合式 pipeline 引擎（`processing/pipeline.py`）：

| Stage | 模块 | 输入 | 输出 |
|-------|------|------|------|
| Fetch | `orchestrator.py` | RSS Feed JSON | 文章正文 + 图片列表 |
| Vision | `processing/vision.py` | 图片 URL 列表 | 图片语义描述（`ImageDescription[]`） |
| LLM | `processing/llm.py` | 正文 + Skill 规范 + 图片描述 | 结构化 JSON（分类/摘要/概念/正文） |
| Markdown | `processing/markdown.py` + `output/validator.py` | 结构化 JSON | Obsidian Markdown（格式校验通过） |
| Image | `processing/images.py` | Markdown + 图片列表 | 插入图片后的 Markdown |
| Write | `orchestrator.py` | Markdown + 元数据 | Vault 文件 + processed.json 更新 |

Vision API 不可用时自动跳过 Vision stage，LLM 以纯文本模式生成，Image stage 降级到关键词匹配插入图片。

### 知识图谱

系统自动维护三层关联关系：

| 关联类型 | 机制 | 说明 |
|---------|------|------|
| 文章 -> 概念 | LLM 提取关键概念 | 文章底部的 `[[概念名]]` 双向链接 |
| 概念 -> 文章 | 自动追加 backlink | 概念页面 `## 相关文章` 自动聚合引用该概念的所有文章 |
| 文章 -> 文章 | 混合关联算法 | 每篇最多关联 3 篇，阈值 0.1 |

混合关联算法公式：

```
score = 0.50 x BM25 + 0.20 x concept_sim + 0.20 x tag_sim + 0.10 x sub_topic_bonus
```

- **BM25**：SQLite FTS5 全文检索，jieba 中文分词预处理，sigmoid 归一化
- **概念模糊匹配**：子串包含 -> 1.0，编辑距离 ratio <= 0.3 -> 0.8，非对称 max-avg
- **标签 Jaccard**：按 `_` 分割为 token 集合后计算 Jaccard 相似度
- **子主题加分**：同 `sub_topic` 的文章获得额外加分
- **RRF k 参数**：随语料库大小线性插值，k = 15 + 45 * min(1, N / 300)

索引持久化在 `~/.wx-obsidian/similarity.sqlite`，支持增量更新和双向关联推荐。

### Vault 目录结构

```
Obsidian Vault/
└── 公众号文章/
    ├── _MOC.md               <- 总目录（自动更新）
    ├── LLM基础/
    │   ├── sub_topic/        <- 3 篇同主题后自动创建
    │   └── *.md              <- 文章笔记
    ├── Agent/
    ├── Prompt工程/
    ├── Z归档/                 <- 按日期归档
    │   └── 26/06/
    │       └── 07.md
    └── 概念/
        ├── Transformer.md    <- 概念页面（自动聚合相关文章）
        └── RAG.md
```

## 项目结构

```
wx-obsidian/
├── process_articles.py          # 统一入口（CLI / TUI / Web / delete / migrate-vault 子命令）
├── validate_markdown.py         # Markdown 格式校验（向后兼容入口）
├── deploy/we-mp-rss/            # 数据源部署（派生镜像 + 发布时间回填补丁 + 配置）
├── packaging/                   # macOS 打包
│   ├── build_app.sh             # 构建 .app（3.12 venv + PyInstaller + ad-hoc 签名 + 自检）
│   ├── wx-obsidian.spec         # PyInstaller 配置（资源、hiddenimports、Info.plist）
│   ├── entry_web.py             # 冻结包入口（driving web.main）
│   ├── make_icon.py             # 生成 AppIcon.icns
│   ├── AppIcon.icns             # 图标（脚本生成，入库保证可复现）
│   └── smoke_frozen.py          # 冻结包端到端冒烟（临时 HOME + stub 上游，零费用）
├── pyproject.toml               # Python 项目配置 + ruff/mypy 配置
├── .env.example                 # 环境变量模板
├── run.sh                       # 手动跑一轮的启动脚本（./run.sh [--limit N]）
│
├── prompts/
│   ├── summarize_article.txt    # 主 prompt（string.Template, $variable 占位符）
│   └── refine_with_images.txt   # 图片修订 prompt
│
├── skills/                      # AI 输出控制 Skill（改 Skill 即改笔记风格）
│   ├── article-body/SKILL.md    # 章节结构（4-6 节）、写作风格、图片规范
│   ├── classification/SKILL.md  # category 选择（15 个预设）、sub_topic 提取规则
│   └── note-metadata/SKILL.md   # frontmatter 格式、摘要、概念提取、相关主题
│
└── wx_obsidian/                 # 核心 Python 包
    ├── cli.py                   # CLI 入口（argparse, 调用 Orchestrator）
    ├── orchestrator.py          # 核心编排器（6-stage pipeline, TUI/CLI 共享）
    ├── config_manager.py        # 配置管理（~/.wx-obsidian/config.json, 连通性测试）
    ├── config.py                # 旧版配置读取（processed.json, .env, Skill 加载）
    ├── models.py                # 全局数据模型（ProcessingResult, HealthStatus, Feed 等）
    ├── batch.py                 # 批量并行处理 + 指数退避重试
    ├── scheduler.py             # 应用内定时调度（APScheduler，仅应用运行期间生效）
    ├── vault_migration.py       # 换 Vault 路径时修正配置与 processed.json 的绝对路径
    ├── web/                     # Web 界面（与 tui/、cli.py 平级，只依赖 Orchestrator）
    │   ├── runtime.py           # 装配 ConfigManager + 数据源 + Orchestrator + Scheduler
    │   ├── api.py               # FastAPI 应用工厂（REST + SSE + 安全中间件 + 静态托管）
    │   ├── jobs.py              # 管线任务单飞 + 事件缓冲（线程安全的进度桥接）
    │   ├── articles.py          # 文章列表投影 / 级联删除 / Obsidian 跳转
    │   ├── fs.py                # 目录浏览 + macOS 原生目录选择面板（osascript）
    │   ├── logs.py              # 日志环形缓冲（WARNING+，供页面看系统告警）
    │   ├── scheduling.py        # 定时任务：设定持久化到 config.json，重启自动恢复
    │   ├── logging_setup.py     # 日志落盘 + 轮转 + 密钥掩码 + stdout/stderr 接管
    │   ├── __main__.py          # 启动器（选端口 / 单实例交接 / 开浏览器 / 退出接口）
    │   ├── frontend/            # Vue 3 + Vite + Element Plus 源码
    │   └── static/              # 前端构建产物（pnpm build 生成，不入库）
    ├── sources/
    │   ├── base.py              # ArticleSource 协议（编排层只依赖此抽象）
    │   ├── factory.py           # 数据源工厂（按 WX_SOURCE / source.type 选择）
    │   ├── we_mp_rss.py         # we-mp-rss API 客户端
    │   └── rss.py               # 取文契约 + 微信文章正文提取
    │
    ├── processing/
    │   ├── pipeline.py          # Pipeline 引擎（函数组合式 stage）
    │   ├── models.py            # Pipeline 数据模型（PipelineContext, ImageDescription）
    │   ├── vision.py            # 多模态 Vision API（广告过滤 + 并发调用）
    │   ├── llm.py               # DeepSeek API 调用 + prompt 构建 + 响应解析
    │   ├── images.py            # 图片提取 + 语义匹配 + 自动插入
    │   ├── markdown.py          # Markdown 生成（frontmatter + body + 假图片清除）
    │   └── similarity.py        # 文章相似度（SQLite FTS5 + jieba + BM25F + RRF）
    │
    ├── output/
    │   ├── vault.py             # Obsidian Vault 操作（MOC、概念页面、分类、子目录）
    │   ├── validator.py         # Markdown 格式校验与自动修复
    │   └── cleanup.py           # 文章级联删除
    │
    └── tui/                     # Textual TUI 界面
        ├── app.py               # TUI 主应用
        ├── screens/             # Home/Container/Account/Feeds/Config/Fetch/Scheduler
        └── widgets/             # 自定义组件（状态指示器等）
```

### 依赖关系

```
cli.py / tui/app.py
    └── orchestrator.py        # 核心编排，被 CLI 和 TUI 共享
            ├── sources/rss.py         # 数据获取
            ├── processing/*           # 处理层
            ├── output/*               # 输出层
            └── batch.py               # 并行执行

config.py（最底层，被所有模块依赖）
config_manager.py（配置管理 + 连通性测试，被 orchestrator/tui 依赖）
```

`sources/`、`processing/`、`output/` 之间不互相依赖，通过 orchestrator 编排。

## Skill 系统

3 个 Skill 文件协同控制 AI 输出，修改 Skill 即可调整笔记风格，无需改代码：

| Skill | 路径 | 职责 |
|-------|------|------|
| `article-body` | `skills/article-body/SKILL.md` | 章节结构（4-6 节）、写作风格（"拓展思路，而非罗列信息"）、表格使用、图片插入规范 |
| `classification` | `skills/classification/SKILL.md` | category 选择（15 个预设）、sub_topic 提取规则、分类边界判断 |
| `note-metadata` | `skills/note-metadata/SKILL.md` | frontmatter 格式、摘要、概念提取、相关主题 |

Prompt 模板 `prompts/summarize_article.txt` 使用 `string.Template` 的 `$variable` 占位符，运行时注入 3 个 Skill 内容 + 已有知识库索引。

## 分类体系

4 组 15 个主分类，支持运行时动态新增：

| 分类组 | 分类 |
|--------|------|
| 基础领域 | LLM基础、Agent、多Agent协同、上下文工程、多模态与视觉、具身智能 |
| 技术专题 | Prompt工程、RAG、训练与微调、AI基础设施、AI编程、MCP |
| 工程实践 | Harness Engineering |
| 产业 | AI产业生态 |
| 兜底 | 其他 |

同一 `sub_topic` 积累 3 篇文章后，自动创建子目录聚合。

## 格式保障

每篇文章生成后经过三层格式保障：

1. **自动修复**（`validate_and_fix`）：frontmatter 校验、代码块闭合、表格语法、全角字符、连续空行
2. **结构性检测**（`detect_format_issues`）：基于 mistune 解析，检测表格渲染失败、代码块未闭合等自动修复无法处理的问题
3. **LLM 反馈修正**（`fix_format_issues`）：将结构性问题反馈给 LLM 重新生成，再校验直到通过

## 生成笔记示例

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
本文深度拆解了 Claude Code 的工程架构，核心论点是其竞争力源于 12 层渐进式包装...

## 核心观点
- **胖核心设计**：query.ts 刻意保持 785KB 单文件，保证核心循环原子性
- **开闭原则**：所有扩展不修改核心循环，新增功能 = 新增工具/包装层

## 一、核心架构
Claude Code 采用了"胖核心"设计，query.ts 刻意保持 785KB 单文件...

![架构全景图](https://mmbiz.qpic.cn/...)

## 关键概念
- [[Progressive Harness]]：渐进式工程包装，在极简内核外逐层叠加生产级特性
- [[Context Engineering]]：构建高信噪比上下文供给系统的工程方法论

## 相关主题
- [[另一篇已有的文章]]
- [[RAG]]
```

> 来源信息（`source`、`url`）已包含在 frontmatter 中，正文不再重复。

## 配置说明

### 环境变量（.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key（语言模型与视觉模型默认共用） |
| `DEEPSEEK_BASE_URL` | 否 | API 地址，默认 `https://api.deepseek.com` |
| `MODEL_NAME` | 否 | 模型名，默认 `deepseek-flash` |
| `WX_SOURCE` | 否 | 数据源类型，当前仅 `we_mp_rss`（临时覆盖 `source.type`） |
| `DEBUG` | 否 | 设为 `1` 开启调试（保存 API 原始响应到 `last_response.txt`） |
| `VISION_API_KEY` | 否 | 独立视觉渠道 Key，留空则与 LLM 共用 |
| `VISION_BASE_URL` | 否 | Vision API 地址，默认 `https://api.deepseek.com` |
| `VISION_MODEL_NAME` | 否 | Vision 模型名，默认 `deepseek-flash` |
| `MAX_VISION_CONCURRENCY` | 否 | Vision 并发数，默认 `10` |

> 模型渠道也可完全绕开 `.env`：在 `config.json` 的 `llm.providers` / `vision.providers`
> 下新增任意 OpenAI 兼容渠道，再把 `llm.active` / `vision.active` 指过去即可切换。

### 项目配置（`~/.wx-obsidian/config.json`）

由 `ConfigManager` 管理，TUI 的 Config 界面可直接编辑。关键字段：

```jsonc
{
  "source":   { "type": "we_mp_rss", "base_url": "http://localhost:8001", "timeout": 15 },
  "fetch":    { "max_days": 7, "max_workers": 5, "feed_limit": 300 },
  "llm":      { "active": "deepseek", "providers": { "deepseek": { "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key_env": "DEEPSEEK_API_KEY" } } },
  "vision":   { "active": "deepseek", "providers": { "deepseek": { "model": "deepseek-flash" } }, "inline_images": true },
  "obsidian": { "vault_path": "/path/to/vault", "articles_dir": "公众号文章" },
  "web":      { "port": 8765, "open_browser": true, "log_level": "INFO" },
  "scheduler":{ "fetch_cron": "0 */2 * * *", "keepalive_interval_days": 7 },
  "categories": ["LLM基础", "Agent", "..."]
}
```

> `web.port` / `web.open_browser` 由 .app 启动器读取；`scheduler.*` 由定时任务页写入（重启后自动恢复）。

## 开发

```bash
# 代码检查
ruff check .

# 格式化
ruff format .

# 类型检查
mypy wx_obsidian/ process_articles.py validate_markdown.py

# 校验 Markdown 文件
python validate_markdown.py <file.md>
python validate_markdown.py --dir <directory>
```

### 编码规范

- Python 3.9+，使用 `from __future__ import annotations`
- Ruff：line-length 100, target py39, E501 除外（f-string prompt 模板无法拆行）
- mypy strict 模式
- 路径安全：LLM 返回的 category/sub_topic 经 `re.sub(r'[<>:"/\\|?*]', "_", ...)` 过滤
- 原子写入：持久化文件使用 `tempfile + os.replace`
- 异常规范：禁止裸 `except:`，外部 API 必须有 timeout
- 依赖隔离：`sources/`、`processing/`、`output/` 之间不互相依赖

## License

[MIT](LICENSE) © malog
