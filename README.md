# wx-obsidian

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/DeepSeek_API-Compatible-orange" alt="DeepSeek">
  <img src="https://img.shields.io/badge/DashScope_Vision-Multimodal-red" alt="DashScope Vision">
  <img src="https://img.shields.io/badge/WeWe_RSS-Docker-green" alt="WeWe RSS">
  <img src="https://img.shields.io/badge/Obsidian-Compatible-purple" alt="Obsidian">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

微信公众号文章 -> Obsidian 知识库自动化处理器。自动抓取公众号文章，通过 LLM 生成结构化笔记，写入 Obsidian Vault 并维护双向链接知识图谱。

## 核心特性

- **CLI + TUI 双模式**：命令行批量处理 + [Textual](https://textual.textualize.io/) 终端图形界面
- **全自动抓取**：通过 WeWe RSS 定时拉取公众号文章，增量处理不重复
- **批量并行处理**：ThreadPoolExecutor 并行处理多篇文章，指数退避重试（最多 3 次，覆盖 429/5xx/网络错误）
- **多模态 Vision**：DashScope 视觉模型识别文章图片，为 LLM 提供语义描述，无 Vision 时自动降级到纯文本 + 关键词匹配
- **3-Skill 系统**：模块化控制输出风格，写作风格、分类规则、元数据格式独立配置，改 Skill 即改笔记风格
- **智能分类**：15 个预设分类 + 动态新增，同一子主题积累 3 篇后自动创建子目录
- **知识图谱**：`[[双向链接]]` 关联已有文章和概念页面，Obsidian Graph View 自动成图
- **混合关联**：SQLite FTS5 全文检索 + jieba 分词 + 概念模糊匹配 + 标签 Jaccard，自动计算文章间相似度并持久化索引
- **级联删除**：一键删除文章及其所有关联数据（MOC 条目、概念页面、归档、processed.json）
- **格式保障**：自动校验修复 + mistune 结构性检测 + LLM 反馈修正，三层格式保障
- **定时调度**：APScheduler 集成，支持 cron 定时抓取和处理

## Quick Start

### 前置条件

- Python 3.9+
- Docker（用于部署 WeWe RSS）
- [DeepSeek API Key](https://platform.deepseek.com/)（必填）
- [DashScope API Key](https://dashscope.console.aliyun.com/)（可选，用于多模态图片识别）

### 1. 安装

```bash
git clone https://github.com/yourname/wx-obsidian.git
cd wx-obsidian

# uv（推荐）
uv sync

# 或 pip
pip install -e .
```

### 2. 启动 WeWe RSS

```bash
docker compose up -d
```

访问 http://localhost:4000，用微信扫码登录，添加要订阅的公众号。

### 3. 配置

**方式一：TUI 界面配置（推荐）**

```bash
python process_articles.py tui
```

进入 Config 界面，填写配置并点击「测试连通性」确认：

| 配置项 | 必填 | 说明 |
|--------|------|------|
| DeepSeek API Key | 是 | [获取地址](https://platform.deepseek.com/) |
| Vision API Key | 否 | DashScope API Key，留空禁用多模态 |
| 知识库路径 | 是 | Obsidian Vault 路径（支持目录选择器） |
| WeWe RSS 服务地址 | 否 | 默认 `http://localhost:4000` |

**方式二：手动编辑配置文件**

```bash
cp .env.example .env
# 编辑 .env 填写 API Key
# 编辑 config.yaml 设置 vault_path 等
```

### 4. 抓取并处理文章

```bash
# 处理所有未处理的文章
python process_articles.py

# 只处理 5 篇（试跑）
python process_articles.py --limit 5
```

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
| Container | WeWe RSS 容器状态监控、启停控制 |
| Account | 微信登录状态、扫码保活 |
| Feeds | 公众号订阅管理 |
| Config | LLM / Vision / Obsidian 配置编辑、连通性测试 |
| Fetch | 手动触发文章抓取与处理 |
| Scheduler | 定时任务管理（cron 表达式配置） |

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
    └── TUI 模式 -> wx_obsidian/tui/   -> Orchestrator

Orchestrator（核心编排器）
    │
    ├─ Stage 1: Fetch       <- WeWe RSS 拉取文章 + 正文提取
    ├─ Stage 2: Vision      <- DashScope 视觉模型识别图片（可选，失败自动降级）
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
├── process_articles.py          # 统一入口（CLI / TUI / delete 子命令）
├── validate_markdown.py         # Markdown 格式校验（向后兼容入口）
├── config.yaml                  # 项目配置（Vault 路径、分类列表、WeWe RSS 地址）
├── docker-compose.yml           # WeWe RSS + MySQL 部署
├── pyproject.toml               # Python 项目配置 + ruff/mypy 配置
├── .env.example                 # 环境变量模板
├── run.sh                       # 定时任务启动脚本（加载 .env + 覆盖模型）
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
    ├── scheduler.py             # 定时任务调度（APScheduler AsyncIOScheduler）
    ├── wewe_rss.py              # WeWe RSS tRPC API 客户端
    │
    ├── sources/
    │   └── rss.py               # RSS Feed 解析 + 文章正文抓取
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
| `DEEPSEEK_API_KEY` | 是 | DeepSeek API Key |
| `DEEPSEEK_BASE_URL` | 否 | API 地址，默认 `https://api.deepseek.com` |
| `MODEL_NAME` | 否 | 模型名，默认 `deepseek-chat` |
| `AUTH_CODE` | 否 | WeWe RSS 认证码 |
| `CRON_EXPRESSION` | 否 | 抓取定时，默认 `0 */2 * * *`（每 2 小时） |
| `DEBUG` | 否 | 设为 `1` 开启调试（保存 API 原始响应到 `last_response.txt`） |
| `VISION_API_KEY` | 否 | DashScope API Key，留空禁用多模态 |
| `VISION_BASE_URL` | 否 | Vision API 地址，默认 DashScope 兼容模式 |
| `VISION_MODEL_NAME` | 否 | Vision 模型名，默认 `qwen-vl-plus` |
| `MAX_VISION_CONCURRENCY` | 否 | Vision 并发数，默认 `10` |

### 项目配置（config.yaml）

```yaml
obsidian:
  vault_path: /path/to/obsidian/vault    # Obsidian Vault 路径
  articles_dir: 公众号文章                # 文章目录名
wewe_rss:
  base_url: http://localhost:4000        # WeWe RSS 地址
  auth_code: your-auth-code              # 认证码
deepseek:
  model: deepseek-chat                   # LLM 模型
max_workers: 5                           # 并行处理线程数
categories:                              # 分类列表（运行时自动追加新分类）
  - LLM基础
  - Agent
  - ...
```

### 用户级配置（~/.wx-obsidian/config.json）

TUI Config 界面保存的配置，优先级高于 `.env` 和 `config.yaml`。

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
