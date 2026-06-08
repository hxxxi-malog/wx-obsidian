---
name: article-classification
description: 公众号文章的分类与子主题提取规范，用于自动归类和子目录聚合
---

# 文章分类规范

## category 选择

**已有主分类：**

| 分类组 | 分类 |
| --- | --- |
| 模型层 | LLM基础、训练与微调、多模态与视觉、具身智能 |
| Agent 层 | Agent（含多Agent协同、RAG、MCP、上下文工程、Prompt工程） |
| 应用层 | AI编程、后端技术 |
| 工程层 | Harness Engineering |
| 生态层 | AI产业生态 |
| 兜底 | 其他 |

**选择规则：**
1. 优先匹配已有分类
2. 确实不属于任何已有分类时，创建新分类
3. 新分类命名：简洁的中文或英文术语
4. 冲突时专题优先于基础领域（如讲推理优化 → LLM基础；讲微调 → 训练与微调）
5. LLM基础 仅保留模型原理/架构/论文解读、推理优化/部署/量化等模型层内容
6. CV/NLP 内容统一归入多模态与视觉或 LLM基础

**判断边界（专题优先于基础领域）：**
- 文章重心在模型架构/原理/论文解读/推理优化/部署/量化 → LLM基础
- 文章重心在微调/训练策略/RLHF/DPO → 训练与微调
- 文章重心在单Agent架构/工具调度/子代理/记忆系统/权限模型 → Agent
- 文章重心在多Agent协同/编排/团队协作/任务分配/通信协议 → Agent
- 文章重心在检索增强/向量数据库/Agentic RAG/GraphRAG → Agent
- 文章重心在 MCP 协议/工具接入/A2A → Agent
- 文章重心在上下文管理/压缩/知识注入/Context Engineering → Agent
- 文章重心在 Prompt 技巧/模板/优化策略 → Agent
- 文章重心在 AI 辅助编程/Cursor/Copilot/代码生成 → AI编程
- 文章重心在后端架构/数据库/分布式/微服务/Java/Go → 后端技术
- 文章重心在 Harness/工程化/流程约束/多Agent协作规范 → Harness Engineering
- 文章重心在视觉/图像/视频/多模态理解生成 → 多模态与视觉
- 文章重心在具身智能/机器人/embodied AI → 具身智能
- 文章重心在行业动态/产品发布/公司战略 → AI产业生态
- 多主题交叉 → 取文章重心所在分类（专题 > 基础领域）

## sub_topic 提取

用 2-4 个词概括文章的核心子主题方向，用于同一分类下相似文章的自动聚合。

**规则：**
- 必须是能聚合多篇文章的通用概念，非单篇文章标题
- 粒度适中：太粗（"AI技术"）无区分度，太细（"GPT-4的attention优化"）无法聚合
- 文章无明显子主题倾向时留空

**Agent 分类下的常见 sub_topic：**

| sub_topic | 覆盖范围 |
| --- | --- |
| Agent架构 | 单Agent设计模式/ReAct/工具调用/记忆系统 |
| 多Agent协同 | 多Agent编排/Orchestrator/团队协作/通信协议 |
| RAG | 检索增强/向量数据库/Agentic RAG/GraphRAG |
| MCP | MCP协议/工具接入规范/A2A |
| 上下文工程 | 上下文压缩/知识注入/Context Engineering |
| Prompt工程 | Prompt技巧/模板/few-shot/思维链 |

**其他示例：**

| 文章标题 | category | sub_topic |
| --- | --- | --- |
| Transformer注意力优化新思路 | LLM基础 | 注意力机制 |
| vLLM推理引擎深度拆解 | LLM基础 | 推理引擎 |
| LoRA微调实战指南 | 训练与微调 | LoRA |
| Stable Diffusion 3架构拆解 | 多模态与视觉 | 图像生成 |
| Claude Code的上下文压缩机制 | Agent | 上下文工程 |
| 多Agent协同开发实战 | Agent | 多Agent协同 |
| RAG vs Fine-tuning选型指南 | Agent | RAG |

**sub_topic 粒度参考：**
- ✅ 好：多Agent协同、向量检索、上下文压缩、Prompt优化、模型量化
- ❌ 太粗：AI技术、深度学习、自然语言处理
- ❌ 太细：Claude Code v2.1的snipCompact机制

## 分类与目录的关系

```
公众号文章/
├── LLM基础/                ← 模型原理/架构/推理优化/部署/量化
├── Agent/                  ← Agent 全谱系（单Agent/多Agent/RAG/MCP/上下文/Prompt）
│   ├── 多Agent协同/        ← sub_topic 自动拆分（≥3篇后）
│   ├── RAG/
│   ├── MCP/
│   ├── 上下文工程/
│   └── Prompt工程/
├── 多模态与视觉/           ← CV/图像/视频/多模态
├── 具身智能/               ← 机器人/embodied AI
├── 训练与微调/             ← 微调/RLHF/DPO
├── AI编程/                 ← Cursor/Copilot/代码生成
├── 后端技术/               ← 后端架构/数据库/分布式/微服务
├── Harness Engineering/    ← 工程化/流程约束
├── AI产业生态/             ← 行业动态/产品发布
├── 其他/
└── 概念/                   ← 概念双向链接页面
```

- 文章始终先放入主分类目录
- 同一 sub_topic 积累 3 篇后，自动创建子目录并迁移
- sub_topic 不足 3 篇的文章留在主分类目录，不影响使用
