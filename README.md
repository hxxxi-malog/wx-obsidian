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

- **全自动抓取**：通过 WeWe RSS 定时拉取公众号文章，增量处理不重复
- **两轮 LLM 架构**：Pass 1 纯文本生成结构化笔记，Pass 2 结合图片描述修订正文并智能插入图片
- **多模态 Vision**：DashScope 视觉模型识别文章图片，为 LLM 提供图片语义描述
- **3-Skill 系统**：模块化控制输出风格，写作风格、分类规则、元数据格式独立配置
- **智能分类**：18 个预设分类 + 动态新增，同一子主题积累 3 篇后自动创建子目录
- **知识图谱**：`[[双向链接]]` 关联已有文章和概念页面，Obsidian Graph View 自动成图
- **格式保障**：每篇文章生成后自动执行 Markdown 校验与修复
- **优雅降级**：Vision API 不可用时自动跳过 Pass 2，降级到纯文本 + 关键词匹配

## 架构

```
WeWe RSS (Docker, localhost:4000)
    ↓ JSON Feed（定时拉取）
process_articles.py
    ↓ Pipeline: 7 个 Stage 串行执行
    │
    ├─ Stage 1: Fetch       ← 提取文章信息 + 正文内容
    ├─ Stage 2: Vision      ← DashScope 视觉模型识别图片（可选）
    ├─ Stage 3: LLM Pass 1  ← DeepSeek 生成结构化笔记（纯文本）
    ├─ Stage 4: LLM Pass 2  ← DeepSeek 结合图片描述修订正文（可选）
    ├─ Stage 5: Markdown    ← 生成 Obsidian Markdown + 格式校验
    ├─ Stage 6: Image       ← 替换 [IMG:N] 占位符为图片
    └─ Stage 7: Write       ← 写入 Vault + 更新 MOC + 概念页面
    ↓
Obsidian Vault/
    └── 公众号文章/
        ├── _MOC.md               ← 总目录（自动更新）
        ├── LLM/
        │   ├── sub_topic/        ← 3 篇同主题后自动创建
        │   └── *.md              ← 文章笔记
        ├── Agent架构/
        ├── Prompt Engineering/
        ├── RAG/
        ├── ...
        └── 概念/
            ├── Transformer.md    ← 概念页面（聚合相关文章）
            └── RAG.md
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

## 快速开始

### 环境要求

- Python 3.9+
- Docker（用于部署 WeWe RSS）
- DeepSeek API Key
- DashScope API Key（可选，用于多模态图片识别）

### 1. 部署 WeWe RSS

```bash
# 启动 WeWe RSS + MySQL
docker compose up -d

# 访问 http://localhost:4000，用微信扫码登录
# 添加要订阅的公众号
```

### 2. 配置环境变量

```bash
# 复制配置模板
cp .env.example .env

# 编辑 .env，填入你的 API Key
vim .env
```

`.env` 配置项：

**DeepSeek API（必填）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 必填 |
| `DEEPSEEK_BASE_URL` | API 地址 | `https://api.deepseek.com` |
| `MODEL_NAME` | 模型名称 | `deepseek-chat` |

**多模态 Vision API（可选，留空则禁用多模态，自动降级到纯文本）**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `VISION_API_KEY` | DashScope API 密钥 | 留空禁用 |
| `VISION_BASE_URL` | Vision API 地址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `VISION_MODEL_NAME` | 视觉模型名称 | `qwen-vl-plus` |
| `MAX_VISION_CONCURRENCY` | 图片识别并发数 | `10` |
| `VISION_TIMEOUT` | Vision API 超时（秒） | `120` |
| `VISION_MAX_RETRIES` | 重试次数 | `2` |

**其他配置**

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `AUTH_CODE` | WeWe RSS 认证码 | `wxkb2026` |
| `CRON_EXPRESSION` | 文章抓取定时 | `0 */2 * * *` |
| `DEBUG` | 调试模式（保存 API 原始响应） | `0` |

### 3. 配置 Obsidian Vault 路径

编辑 `config.yaml`，修改 `vault_path` 为你的 Obsidian Vault 路径：

```yaml
obsidian:
  vault_path: "/path/to/your/obsidian/vault"
  articles_dir: "公众号文章"
```

### 4. 安装依赖并运行

```bash
# 安装依赖
pip install requests pyyaml

# 运行（处理所有未处理的文章）
python process_articles.py

# 只处理指定数量
python process_articles.py --limit 5
```

### 5. 定时运行（可选）

```bash
# 使用 run.sh（自动加载环境变量）
chmod +x run.sh
./run.sh

# 或配置 crontab
0 */2 * * * cd /path/to/wx-obsidian && python process_articles.py >> logs/run.log 2>&1
```

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

## 二、工具调度机制
工具调度遵循开闭原则，所有扩展不修改核心循环...

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

## 项目结构

```
wx-obsidian/
├── process_articles.py          # CLI 入口
├── validate_markdown.py         # Markdown 格式校验与自动修复
├── config.yaml                  # 项目配置（Vault 路径、分类列表）
├── docker-compose.yml           # WeWe RSS + MySQL 部署
├── run.sh                       # 定时运行脚本
├── pyproject.toml               # Python 项目配置
├── .env.example                 # 环境变量模板
├── prompts/                     # LLM Prompt 模板
│   ├── summarize_article.txt    # Pass 1：纯文本生成
│   └── refine_with_images.txt   # Pass 2：图文修订
├── skills/                      # AI 输出控制 Skill
│   ├── article-body/            # 写作风格 + 图片规范
│   ├── classification/          # 分类规则
│   └── note-metadata/           # 元数据与链接
├── wx_obsidian/                 # 核心 Python 包
│   ├── cli.py                   # 流程编排（7 Stage Pipeline）
│   ├── config.py                # 配置/持久化
│   ├── sources/rss.py           # WeWe RSS 抓取
│   ├── processing/
│   │   ├── vision.py            # 多模态 Vision API
│   │   ├── llm.py               # DeepSeek 两轮调用
│   │   ├── images.py            # 图片提取
│   │   ├── markdown.py          # Markdown 生成
│   │   ├── models.py            # 数据模型
│   │   └── pipeline.py          # Pipeline 引擎
│   └── output/
│       ├── vault.py             # Obsidian Vault 操作
│       └── validator.py         # 格式校验
├── docs/                        # 设计文档
└── data/                        # WeWe RSS 数据目录
```

## 工作原理

两轮 LLM + 多模态 Vision 的处理流程：

```
1. fetch_articles()                    ← WeWe RSS JSON Feed 拉取文章列表
2. _fetch_stage()                      ← 提取文章信息 + 正文内容（Feed 不足时从 URL 抓取）
3. _vision_stage()                     ← DashScope 视觉模型识别图片 → image_descriptions
4. _llm_pass1_stage()                  ← DeepSeek 纯文本生成 → body_sections（不看图片）
5. _llm_pass2_stage()                  ← DeepSeek 结合原文 + 图片描述修订 → [IMG:N] 占位符
6. _markdown_stage()                   ← 生成 Obsidian Markdown + 格式校验
7. _image_stage()                      ← 替换 [IMG:N] → ![purpose](url)
8. _write_stage()                      ← 写入文件 → 创建概念页面 → 更新 MOC → 检查子目录拆分
```

**两轮 LLM 架构**：
- **Pass 1**（纯文本）：不看图片，专注生成结构化笔记的骨架（摘要、核心观点、详细拆解）
- **Pass 2**（图文修订）：拿到 Pass 1 输出 + Vision 图片描述，修订正文并在合适位置嵌入 `[IMG:N]` 占位符
- **降级策略**：Vision API 不可用 → 跳过 Pass 2 → Pass 1 结果 + 关键词匹配插入图片

关键设计：
- **增量处理**：`processed.json` 记录已处理文章 ID，跳过重复
- **原子进度**：每处理完一篇立即保存进度，中断不丢数据
- **路径安全**：LLM 返回的分类名经过正则清洗，防止路径注入
- **概念聚合**：相同概念被多篇文章引用时，自动创建概念页面形成图谱节点
- **相关链接**：扫描已有知识库，只链接已存在的文章和概念，不创建死链接

## 开发

```bash
# 安装开发依赖
pip install ruff mypy pytest

# 代码检查
ruff check .

# 格式化
ruff format .

# 类型检查
mypy wx_obsidian/ process_articles.py validate_markdown.py

# 运行测试
pytest tests/
```

## License

MIT
