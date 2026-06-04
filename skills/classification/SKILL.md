---
name: article-classification
description: 公众号文章的分类与子主题提取规范，用于自动归类和子目录聚合
---

# 文章分类规范

## category 选择

**已有主分类：**

| 分类组 | 分类 |
| --- | --- |
| 基础领域 | LLM、Agent架构、多Agent协同、上下文工程、多模态、NLP、CV、具身智能 |
| 技术专题 | Prompt Engineering、RAG、模型训练与微调、AI Infra、AI编程、MCP |
| 工程实践 | Harness Engineering、评测、实践 |
| 兜底 | 其他 |

**选择规则：**
1. 优先匹配已有分类
2. 确实不属于任何已有分类时，创建新分类
3. 新分类命名：简洁的中文或英文术语
4. 冲突时专题优先于基础领域（如讲推理优化 → AI Infra，而非 LLM；讲微调 → 模型训练与微调，而非 LLM）
5. LLM 仅保留模型原理/架构/论文解读等"纯模型"内容

**判断边界（专题优先于基础领域）：**
- 文章重心在模型架构/原理/论文解读 → LLM
- 文章重心在微调/训练策略/RLHF/DPO → 模型训练与微调
- 文章重心在推理优化/部署/量化/显存/推理引擎 → AI Infra
- 文章重心在单Agent架构/工具调度/子代理/记忆系统/权限模型 → Agent架构
- 文章重心在多Agent协同/团队协作/任务分配/通信协议 → 多Agent协同
- 文章重心在上下文管理/压缩/知识注入/RAG检索策略 → 上下文工程
- 文章重心在 Prompt 技巧/模板/优化策略 → Prompt Engineering
- 文章重心在检索增强/向量数据库/知识库 → RAG
- 文章重心在 MCP 协议/工具接入规范 → MCP
- 文章重心在 AI 辅助编程/Cursor/Copilot/代码生成 → AI编程
- 文章重心在 Harness/工程化/流程约束/多Agent协作规范 → Harness Engineering
- 文章重心在 CV/NLP/多模态技术 → 对应分类
- 多主题交叉 → 取文章重心所在分类（专题 > 基础领域）

## sub_topic 提取

用 2-4 个词概括文章的核心子主题方向，用于同一分类下相似文章的自动聚合。

**规则：**
- 必须是能聚合多篇文章的通用概念，非单篇文章标题
- 粒度适中：太粗（"AI技术"）无区分度，太细（"GPT-4的attention优化"）无法聚合
- 文章无明显子主题倾向时留空

**示例：**

| 文章标题 | category | sub_topic |
| --- | --- | --- |
| Claude Code的上下文压缩机制 | 上下文工程 | 上下文压缩 |
| 多Agent协同开发实战 | 多Agent协同 | Swarm模式 |
| Agent核心循环设计模式 | Agent架构 | Agent Loop |
| RAG vs Fine-tuning选型指南 | RAG | 选型对比 |
| Transformer注意力优化新思路 | LLM | 注意力机制 |
| vLLM推理引擎深度拆解 | AI Infra | 推理引擎 |
| Stable Diffusion 3架构拆解 | 多模态 | 图像生成 |
| LoRA微调实战指南 | 模型训练与微调 | LoRA |

**sub_topic 粒度参考：**
- ✅ 好：上下文压缩、多Agent协同、向量检索、Prompt优化、模型量化
- ❌ 太粗：AI技术、深度学习、自然语言处理
- ❌ 太细：Claude Code v2.1的snipCompact机制

## 分类与目录的关系

```
公众号文章/
├── LLM/                    ← 基础领域
├── Agent架构/              ← 单Agent设计模式
├── 多Agent协同/            ← 多Agent系统
├── 上下文工程/             ← 上下文管理与知识注入
├── Prompt Engineering/     ← 技术专题
├── RAG/
├── AI Infra/
├── Harness Engineering/    ← 工程实践
├── 评测/
├── 实践/
├── 其他/
└── 概念/                   ← 概念双向链接页面
```

- 文章始终先放入主分类目录
- 同一 sub_topic 积累 3 篇后，自动创建子目录并迁移
- sub_topic 不足 3 篇的文章留在主分类目录，不影响使用
