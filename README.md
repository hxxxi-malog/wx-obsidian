# wx-obsidian

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/DeepSeek_API-Compatible-orange" alt="DeepSeek">
  <img src="https://img.shields.io/badge/DashScope_Vision-Multimodal-red" alt="DashScope Vision">
  <img src="https://img.shields.io/badge/WeWe_RSS-Docker-green" alt="WeWe RSS">
  <img src="https://img.shields.io/badge/Obsidian-Compatible-purple" alt="Obsidian">
  <img src="https://img.shields.io/badge/License-MIT-yellow" alt="License">
</p>

微信公众号文章 → Obsidian 知识库自动化处理器。自动抓取公众号文章，通过两轮 LLM 调用（纯文本生成 + 多模态图文修订）生成结构化笔记，写入 Obsidian Vault 并维护双向链接知识图谱。

## 核心特性

- **CLI + TUI 双模式**：命令行批量处理 + Textual 终端图形界面管理
- **全自动抓取**：通过 WeWe RSS 定时拉取公众号文章，增量处理不重复
- **批量并行处理**：ThreadPoolExecutor 并行处理多篇文章，自动重试失败任务
- **两轮 LLM 架构**：Pass 1 纯文本生成结构化笔记，Pass 2 结合图片描述修订正文并智能插入图片
- **多模态 Vision**：DashScope 视觉模型识别文章图片，为 LLM 提供图片语义描述
- **3-Skill 系统**：模块化控制输出风格，写作风格、分类规则、元数据格式独立配置
- **智能分类**：18 个预设分类 + 动态新增，同一子主题积累 3 篇后自动创建子目录
- **知识图谱**：`[[双向链接]]` 关联已有文章和概念页面，Obsidian Graph View 自动成图
- **定时调度**：APScheduler 集成，支持 cron 定时抓取和处理
- **格式保障**：每篇文章生成后自动执行 Markdown 校验与修复
- **优雅降级**：Vision API 不可用时自动跳过 Pass 2，降级到纯文本 + 关键词匹配

## Quick Start

从零开始跑通整个流程，大约需要 10 分钟。

### 前置条件

- Python 3.9+
- Docker（用于部署 WeWe RSS）
- 一个 [DeepSeek API Key](https://platform.deepseek.com/)
- （可选）一个 [DashScope API Key](https://dashscope.console.aliyun.com/)，用于多模态图片识别

### Step 1: 克隆并安装依赖

```bash
git clone https://github.com/yourname/wx-obsidian.git
cd wx-obsidian

# 方式一：pip
pip install requests pyyaml textual apscheduler

# 方式二：uv（推荐）
uv sync
```

### Step 2: 启动 WeWe RSS

WeWe RSS 负责抓取微信公众号文章，以 Docker 方式部署：

```bash
docker compose up -d
```

启动后访问 http://localhost:4000，用微信扫码登录，然后添加你要订阅的公众号。

### Step 3: 启动 TUI 并配置

```bash
python process_articles.py tui
```

进入 Config 界面，填写：

| 配置项 | 说明 |
|--------|------|
| DeepSeek API Key | 必填，[获取地址](https://platform.deepseek.com/) |
| Vision API Key | 可选，留空则禁用多模态图片识别 |
| 知识库路径 | 你的 Obsidian Vault 路径（支持目录选择器） |
| WeWe RSS 服务地址 | 默认 `http://localhost:4000`，一般不用改 |

每项配置旁边都有「测试连通性」按钮，填完点一下确认没问题，然后点「保存配置」。

### Step 4: 抓取并处理文章

在 TUI 的 Fetch 界面可以手动触发文章抓取与处理。也可以直接用命令行：

```bash
# 处理所有未处理的文章
python process_articles.py

# 只处理 5 篇（试跑）
python process_articles.py --limit 5
```

### Step 5: 查看结果

打开 Obsidian，进入你配置的 Vault，找到 `公众号文章/` 目录。每篇文章生成一个 `.md` 文件，包含 frontmatter 元数据、结构化正文、`[[双向链接]]` 和图片。在 Obsidian 的 Graph View 中可以看到知识图谱自动成图。

## TUI 模式

基于 [Textual](https://textual.textualize.io/) 的终端图形界面，提供完整的管理功能：

```
python process_articles.py tui
```

| 界面 | 功能 |
|------|------|
| Home | 状态概览、快速操作入口 |
| Container | WeWe RSS 容器状态监控、启停控制 |
| Account | 微信登录状态、扫码保活 |
| Feeds | 公众号订阅管理 |
| Config | LLM/Vision/Obsidian 配置编辑、连通性测试 |
| Fetch | 手动触发文章抓取与处理 |
| Scheduler | 定时任务管理（cron 表达式配置） |

## 架构

```
process_articles.py
    ├── CLI 模式 → wx_obsidian/cli.py → Orchestrator
    └── TUI 模式 → wx_obsidian/tui/   → Orchestrator

Orchestrator（核心编排器）
    │
    ├─ Fetch       ← WeWe RSS 拉取文章（支持批量并行）
    ├─ Vision      ← DashScope 视觉模型识别图片（可选）
    ├─ LLM Pass 1  ← DeepSeek 生成结构化笔记（纯文本）
    ├─ LLM Pass 2  ← DeepSeek 结合图片描述修订正文（可选）
    ├─ Markdown    ← 生成 Obsidian Markdown + 格式校验
    ├─ Image       ← 替换 [IMG:N] 占位符为图片
    └─ Write       ← 写入 Vault + 更新 MOC + 概念页面

Obsidian Vault/
    └── 公众号文章/
        ├── _MOC.md               ← 总目录（自动更新）
        ├── LLM/
        │   ├── sub_topic/        ← 3 篇同主题后自动创建
        │   └── *.md              ← 文章笔记
        ├── Agent架构/
        ├── Prompt Engineering/
        └── 概念/
            ├── Transformer.md    ← 概念页面（聚合相关文章）
            └── RAG.md
```

## 项目结构

```
wx-obsidian/
├── process_articles.py          # 统一入口（CLI / TUI 子命令）
├── validate_markdown.py         # Markdown 格式校验（向后兼容入口）
├── config.yaml                  # 项目配置（Vault 路径、分类列表）
├── docker-compose.yml           # WeWe RSS + MySQL 部署
├── run.sh                       # 定时运行脚本
├── pyproject.toml               # Python 项目配置
├── .env.example                 # 环境变量模板
│
├── prompts/                     # LLM Prompt 模板
│   ├── summarize_article.txt    # Pass 1：纯文本生成
│   └── refine_with_images.txt   # Pass 2：图文修订
│
├── skills/                      # AI 输出控制 Skill
│   ├── article-body/            # 写作风格 + 图片规范
│   ├── classification/          # 分类规则
│   └── note-metadata/           # 元数据与链接
│
└── wx_obsidian/                 # 核心 Python 包
    ├── cli.py                   # CLI 入口
    ├── orchestrator.py          # 核心编排器（TUI/CLI 共享）
    ├── config_manager.py        # 配置管理（~/.wx-obsidian/config.json）
    ├── config.py                # 旧版配置读取（兼容）
    ├── models.py                # 全局数据模型
    ├── batch.py                 # 批量并行处理 + 重试
    ├── scheduler.py             # 定时任务调度（APScheduler）
    ├── wewe_rss.py              # WeWe RSS tRPC API 客户端
    ├── sources/
    │   └── rss.py               # RSS Feed 解析 + 文章正文提取
    ├── processing/
    │   ├── pipeline.py          # Pipeline 引擎（函数组合式 stage）
    │   ├── models.py            # Pipeline 数据模型
    │   ├── vision.py            # 多模态 Vision API
    │   ├── llm.py               # DeepSeek 两轮调用
    │   ├── images.py            # 图片提取与插入
    │   └── markdown.py          # Markdown 生成
    ├── output/
    │   ├── vault.py             # Obsidian Vault 操作
    │   └── validator.py         # 格式校验
    └── tui/                     # Textual TUI 界面
        ├── app.py               # TUI 主应用
        ├── screens/             # 各功能界面
        └── widgets/             # 自定义组件
```

## Skill 系统

3 个 Skill 文件协同控制 AI 输出，修改 Skill 即可调整笔记风格，无需改代码：

| Skill | 路径 | 职责 |
|-------|------|------|
| `article-body` | `skills/article-body/SKILL.md` | 控制 4-6 个章节的写作风格、表格使用、重点标记、图片插入规范 |
| `classification` | `skills/classification/SKILL.md` | 控制 category 和 sub_topic 选择规则、判断边界 |
| `note-metadata` | `skills/note-metadata/SKILL.md` | 控制 frontmatter 元数据、摘要、概念提取、相关主题 |

## Prompt 模板

两轮 LLM 调用使用独立的 prompt 模板（`string.Template` 格式）：

| 模板 | 路径 | 用途 |
|------|------|------|
| Pass 1 | `prompts/summarize_article.txt` | 纯文本生成结构化笔记（不含图片决策） |
| Pass 2 | `prompts/refine_with_images.txt` | 结合原文 + 图片描述修订正文，嵌入 `[IMG:N]` 占位符 |

## 生成笔记示例

每篇文章生成一个 `.md` 文件：

```markdown
---
title: "Claude Code 工程架构深度拆解"
source: "AI前线"
author: "张三"
date: 2026-06-04
tags: [Claude_Code, AI_Agent, 工程化, 上下文管理]
category: "Agent架构"
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

---
> 来源：AI前线 | [原文链接](https://mp.weixin.qq.com/...)
```

## 分类体系

4 组 18 个主分类，支持动态新增：

| 分类组 | 分类 |
|--------|------|
| 基础领域 | LLM、Agent架构、多Agent协同、上下文工程、多模态、NLP、CV、具身智能 |
| 技术专题 | Prompt Engineering、RAG、模型训练与微调、AI Infra、AI编程、MCP |
| 工程实践 | Harness Engineering、评测、实践 |
| 兜底 | 其他 |

同一 `sub_topic` 积累 3 篇文章后，自动创建子目录聚合。

## 开发

```bash
# 代码检查
ruff check .

# 格式化
ruff format .

# 类型检查
mypy wx_obsidian/ process_articles.py validate_markdown.py
```

## License

MIT
