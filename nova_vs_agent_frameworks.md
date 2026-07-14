# Nova 与主流 Agent 框架全面对比分析

> 本文档将 Nova 与当前主流 Agent 框架/平台进行全面技术和定位对比，帮助投资人理解 Nova 的差异化价值。

---

## 一、Agent 框架市场分类

当前市场大致分为五类：

| 类别 | 代表 | 核心特征 |
|------|------|---------|
| **通用 Agent 编排框架** | LangChain / LangGraph、AutoGen、CrewAI、OpenAI Agents SDK、Google ADK、Semantic Kernel | 提供 Agent 编排能力，面向开发者 |
| **RAG / 数据框架** | LlamaIndex | 专注检索增强生成和数据连接 |
| **LLM 应用平台** | Dify、Coze、FastGPT | 低代码/无代码平台，可视化工作流 |
| **终端 Coding Agent** | Kimi Code CLI、Codex CLI、Claude Code、Cursor | 强模型 + 强应用，聚焦编程 |
| **个人助理框架** | OpenClaw | 本地优先，连接消息平台 |
| **垂直领域运行时** | **Nova** | 自研企业级 Agent 运行时，锚定生物科技 |

---

## 二、各框架简介

### 2.1 LangChain / LangGraph

- **发布方**：LangChain Inc.
- **定位**：最主流的 LLM 应用编排框架。
- **核心抽象**：Chain、Runnable、AgentExecutor、LangGraph 状态图。
- **优势**：
  - 生态极其丰富，集成数百种工具和服务。
  - 社区成熟，文档和示例众多。
  - LangGraph 提供图状态机编排，支持持久化、Human-in-the-Loop。
- **局限**：
  - 抽象层次深，学习曲线陡峭。
  - 版本迭代快，API 频繁破坏性变更。
  - 通用框架，不懂垂直行业场景。
  - 对本地部署和合规场景支持有限。

### 2.2 AutoGen

- **发布方**：Microsoft
- **定位**：对话式多智能体框架。
- **核心抽象**：Conversable Agent、Group Chat、Code Executor。
- **优势**：
  - 适合研究任务和代码生成工作流。
  - 支持多 Agent 协商、批判、迭代改进。
  - 事件驱动消息基础设施。
- **局限**：
  - 概念复杂，API 变化频繁。
  - 主要用于多 Agent 协作场景。
  - 对单 Agent 长流程、上下文压缩、会话管理等支持较弱。
  - 与微软服务生态强相关。

### 2.3 CrewAI

- **发布方**：CrewAI Inc.
- **定位**：角色扮演多智能体编排框架。
- **核心抽象**：Agent、Task、Crew、Flow。
- **优势**：
  - 概念直观，开箱即用。
  - 适合业务流程自动化和角色分工明确的任务。
  -  reportedly 在结构化业务工作流中准确率提升显著。
- **局限**：
  - 对话模式相对固定。
  - 调试困难，生产稳定性不足。
  - 偏通用，不针对科学计算场景优化。

### 2.4 OpenAI Agents SDK

- **发布方**：OpenAI
- **定位**：轻量级多 Agent 编排 SDK。
- **核心抽象**：Agent、Handoff、Guardrail。
- **优势**：
  - 极简设计，学习曲线低。
  - 与 OpenAI 模型深度集成。
  - 支持 100+ LLM 提供商。
  - Tracing 内置于 OpenAI Traces。
- **局限**：
  - Handoff 是线性链而非图拓扑。
  - 主要面向 OpenAI 生态。
  - 缺乏企业级的会话管理、上下文压缩、包管理。

### 2.5 Google ADK（Agent Development Kit）

- **发布方**：Google
- **定位**：企业级多 Agent 开发框架。
- **核心抽象**：层次化 Agent 树（Root Agent + Sub Agents）、SequentialAgent、ParallelAgent、LoopAgent。
- **优势**：
  - 原生集成 A2A 协议。
  - Gemini 1M+ token 长上下文。
  - Vertex AI 评估和监控工具链。
  - 适合严格流程控制的企业场景。
- **局限**：
  - 学习曲线相对陡。
  - 深度绑定 Google Cloud / Gemini 生态。
  - 对国内生物科技公司合规和本地化支持有限。

### 2.6 Semantic Kernel / Microsoft Agent Framework

- **发布方**：Microsoft
- **定位**：企业级 Agent 开发平台，AutoGen 和 Semantic Kernel 的融合。
- **核心抽象**：Plugins、Planners、Kernel、Agents。
- **优势**：
  - 生产就绪，跨 .NET + Python。
  - 深度集成 Azure、M365、Power Platform。
  - 支持顺序、并发、handoff 等编排模式。
- **局限**：
  - 与微软服务生态强绑定。
  - 学习曲线陡峭。
  - 对国内部署、国产模型、本地化合规支持有限。

### 2.7 LlamaIndex

- **发布方**：LlamaIndex Inc.
- **定位**：RAG 和数据连接框架。
- **核心抽象**：Index、Retriever、Query Engine、Agent。
- **优势**：
  - RAG 场景最强。
  - 支持多种数据源和索引策略。
  - 可以作为 LangChain 等框架的数据层。
- **局限**：
  - 不是通用 Agent 框架。
  - 主要解决"检索+生成"，对工具调用、多 Agent 编排、长流程管理支持有限。

### 2.8 Dify

- **发布方**：LangGenius
- **定位**：开源 LLM 应用开发平台。
- **核心抽象**：Workflow、Chatflow、Agent Node、RAG、Model Management。
- **优势**：
  - 可视化工作流构建。
  - 支持自部署。
  - 适合快速搭建 LLM 应用。
- **局限**：
  - 低代码平台，灵活性和可定制性受限。
  - 对企业级长流程、场景化压缩、本地模型优化支持有限。
  - 更适合应用层，不适合作为底层运行时。

### 2.9 终端 Coding Agent：Kimi Code CLI / Codex CLI / Codex Desktop / Claude Code

#### Kimi Code CLI

- **发布方**：Moonshot AI
- **定位**：终端 AI coding agent
- **核心能力**：读代码、改代码、执行命令、网页搜索
- **优势**：成本低，中文语境优化，模型能力较强
- **局限**：绑定 Moonshot 模型，主要做 coding

#### Codex CLI

- **发布方**：OpenAI
- **定位**：开源终端 coding agent（Rust 构建）
- **核心能力**：read / edit / run code，本地执行
- **优势**：开源、轻量、与 OpenAI 模型深度集成
- **局限**：绑定 OpenAI，单线程终端原生，主要做 coding

#### Codex Desktop App

- **发布方**：OpenAI
- **定位**：原生桌面应用（Mac/Windows），多 Agent 协调中心
- **发布时间**：2026 年 2 月 Mac 版，2026 年 3 月 Windows 版
- **核心能力**：
  - 多 Agent 并行管理
  - 项目（Project）和线程（Thread）管理
  - 持久化项目记忆（跨会话）
  - 本地 / Worktree / Cloud 三种执行模式
  - 内置 Git 工具（diff、comment、stage、revert）
  - 与 Slack、Notion、GitHub 集成
  - 插件生态（90+ plugins）
- **优势**：
  - 可视化控制平面，比 CLI 更适合团队和多任务
  - 多 Agent 并行能力强
  - 与 OpenAI 生态深度集成
- **局限**：
  - 深度绑定 OpenAI 模型和云服务
  - 需要 ChatGPT Plus/Pro/Business/Enterprise 订阅
  - 数据在云端处理，不适合敏感数据
  - 主要面向 coding，不支持生物研发工作流

#### Claude Code

- **发布方**：Anthropic
- **定位**：终端 AI coding agent
- **核心能力**：深度 Agentic coding，subagent 团队，background agent
- **优势**：多 Agent 机制最完整，模型能力强
- **局限**：绑定 Anthropic 模型，主要做 coding

### 2.10 OpenClaw

- **发布方**：社区（Peter Steinberger 等）
- **定位**：本地优先的个人 AI 助理框架。
- **核心抽象**：Channel Layer、Agent Core、Skill Layer。
- **优势**：
  - 本地优先，隐私友好。
  - 连接多种消息平台。
  - Skills 生态增长快。
- **局限**：
  - 主要面向个人助理场景。
  - Node.js/TypeScript 栈，对 Python 科学生态支持有限。
  - 不适合复杂长流程的科学计算 pipeline。

### 2.11 MetaGPT

- **发布方**：DeepWisdom / FoundationAgents
- **定位**：多智能体软件开发框架。
- **核心抽象**：角色（Product Manager、Architect、Engineer 等）、SOP、Assembly Line。
- **优势**：
  - 将软件开发流程编码为多 Agent 协作。
  - 适合端到端软件生成任务。
- **局限**：
  - 高度特化于软件开发。
  - 不适用于生物研发等科学计算场景。
  - 流程僵化，难以定制。

---

## 三、Nova 与各框架的逐项对比

### 3.1 架构层次

| 框架 | 架构特点 | Nova 对比 |
|------|---------|----------|
| LangChain | 组件化组合，抽象层次深 | Nova 分层更清晰，自底向上 |
| AutoGen | 对话式多 Agent | Nova 更关注单 Agent 长流程与企业级管理 |
| CrewAI | 角色扮演 + 任务分配 | Nova 不预设角色，更灵活 |
| OpenAI SDK | 轻量级 handoff | Nova 提供更完整的运行时和基础设施 |
| Google ADK | 层次化 Agent 树 | Nova 分层更细，模型层完全解耦 |
| Semantic Kernel | 企业级 .NET/Python | Nova 更轻量，不绑定微软服务 |
| LlamaIndex | 数据/RAG 层 | Nova 可将其作为数据工具集成 |
| Dify | 低代码平台 | Nova 是底层运行时，更灵活 |
| Kimi/Codex CLI/Desktop | 终端/桌面 coding 应用 | Nova 是框架，可构建类似应用 |
| OpenClaw | 个人助理 | Nova 面向企业科学计算 |
| MetaGPT | 软件开发流水线 | Nova 更通用，可扩展至多种场景 |

### 3.2 模型中立性

| 框架 | 模型绑定程度 |
|------|-------------|
| LangChain | 支持多模型，但生态偏向 OpenAI/Anthropic |
| AutoGen | 支持多模型 |
| CrewAI | 支持多模型 |
| OpenAI SDK | 偏向 OpenAI |
| Google ADK | 偏向 Gemini/Vertex AI |
| Semantic Kernel | 偏向 Azure/OpenAI |
| Kimi Code | 绑定 Moonshot |
| Codex CLI/Desktop | 绑定 OpenAI |
| **Nova** | **模型抽象层，支持国产/本地/海外模型** |

### 3.3 上下文管理

| 框架 | 上下文管理能力 |
|------|---------------|
| LangChain | 简单 Memory，复杂压缩需自行实现 |
| AutoGen | 依赖对话历史，无专门压缩 |
| CrewAI | 任务级上下文，无长流程压缩 |
| OpenAI SDK | 依赖模型长上下文 |
| Google ADK | Gemini 长上下文 |
| Semantic Kernel | 有 Memory 插件，但非场景化 |
| LlamaIndex | RAG 检索，非对话压缩 |
| Dify | 基础上下文管理 |
| **Nova** | **场景化上下文压缩 + 会话树 + 自动/手动压缩** |

### 3.4 工具执行与安全

| 框架 | 工具执行 | 安全机制 |
|------|---------|---------|
| LangChain | Tool 装饰器 | 有限 |
| AutoGen | 代码执行器 | 有沙箱选项 |
| CrewAI | Tool 装饰器 | 有限 |
| OpenAI SDK | 函数调用 | Guardrails |
| Google ADK | FunctionTool | Google Cloud 安全 |
| Semantic Kernel | Plugins | Azure 安全 |
| **Nova** | **校验 + Hook + 并行/串行 + 事件驱动** | **Project Trust + Hook 审批 + 规划中 Sandbox** |

### 3.5 可观测性

| 框架 | 可观测性 |
|------|---------|
| LangChain | LangSmith（付费） |
| AutoGen | 消息日志 |
| CrewAI | 有限 |
| OpenAI SDK | OpenAI Traces |
| Google ADK | Vertex AI 监控 |
| Semantic Kernel | Azure 监控 |
| **Nova** | **原生事件驱动，全链路可观测、可干预、可审计** |

### 3.6 本地部署与合规

| 框架 | 本地部署 | 国产模型 | 数据主权 |
|------|---------|---------|---------|
| LangChain | ⚠️ 需配置 | ⚠️ | ⚠️ |
| AutoGen | ✅ | ⚠️ | ⚠️ |
| CrewAI | ✅ | ⚠️ | ⚠️ |
| OpenAI SDK | ❌ | ❌ | ❌ |
| Google ADK | ❌ | ❌ | ❌ |
| Semantic Kernel | ⚠️ | ❌ | ❌ |
| **Nova** | **✅** | **✅** | **✅** |

### 3.7 垂直行业适配

| 框架 | 垂直行业支持 |
|------|-------------|
| LangChain | 通用，需大量定制 |
| AutoGen | 通用 |
| CrewAI | 通用 |
| OpenAI SDK | 通用 |
| Google ADK | 通用 |
| LlamaIndex | 通用 RAG |
| Dify | 通用应用 |
| **Nova** | **生物科技垂直场景原生设计** |

---

## 四、综合对比表

| 维度 | Nova | LangChain | AutoGen | CrewAI | OpenAI SDK | Google ADK | Dify | Kimi CLI / Codex CLI+Desktop | OpenClaw |
|------|------|-----------|---------|--------|-----------|-----------|------|-----------|----------|
| **自研运行时** | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ |
| **模型中立** | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ | ✅ |
| **场景化压缩** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **会话树** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **事件驱动** | ✅ | ⚠️ | ✅ | ❌ | ❌ | ⚠️ | ❌ | ❌ | ⚠️ |
| **Bundle 专家定制** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ |
| **本地部署** | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ | ⚠️ | ❌ | ✅ |
| **多前端** | ✅ | ❌ | ❌ | ❌ | ❌ | ⚠️ | ⚠️ | ❌ | ✅ |
| **生物场景适配** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **企业级包管理** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ |
| **生产成熟度** | ⚠️ Alpha | ✅ 高 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ 高 | ⚠️ |
| **生态规模** | ⚠️ 小 | ✅ 大 | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ✅ 大 | ⚠️ |

---

## 五、Nova 的差异化定位

### 5.1 不是又一个通用框架

LangChain、AutoGen、CrewAI 都是通用框架，试图解决所有场景的 Agent 编排问题。Nova 选择了一个不同的路径：

> **为需要高度可控、本地部署、长流程、场景化优化的企业科学计算场景，自研一个专业的 Agent 运行时。**

### 5.2 不是模型公司的应用

Kimi、Codex、Claude Code 是模型公司为了展示模型能力做的应用。Nova 是：

> **模型中立的运行时，让企业可以选择最适合自己的模型，而不是被模型公司绑定。**

### 5.3 不是低代码平台

Dify、Coze 让非技术人员快速搭建简单 LLM 应用。Nova 是：

> **面向领域专家的底层基础设施，让真正懂生物的人能造出专业 Agent，同时保持框架层的完全可控。**

### 5.4 不是个人助理

OpenClaw 连接消息平台，做个人助理。Nova 是：

> **企业级科学计算 Agent 的运行时，承载靶点调研、自动化实验、生物信息学分析等复杂长流程任务。**

---

## 六、投资人表达建议

### 6.1 一句话定位

> "Nova 不是 LangChain 的替代品，也不是 Kimi/Codex 这样的 coding 工具。它是一个为生物科技等科学计算场景自研的、模型中立、本地部署、事件驱动的 Agent 运行时。"

### 6.2 三段式对比话术

**第一段：与通用框架对比**

> "LangChain、AutoGen、CrewAI 都是通用框架，它们试图解决所有问题，但这也意味着它们不懂我们的场景。Nova 从设计之初就是为生物研发的长流程、高上下文、强合规需求而设计的。"

**第二段：与模型公司应用对比**

> "Kimi Code、Codex CLI 和 Codex Desktop 都是模型公司做的 coding 应用。Codex Desktop 确实有了多 Agent 和项目记忆，但它深度绑定 OpenAI，数据在云端处理，而且只解决 coding 问题。Nova 模型中立，支持国产和本地私有化部署，承载的是靶点调研、自动化实验、生物信息学分析，不是写代码。"

**第三段：与低代码平台对比**

> "Dify 这类平台适合快速搭简单应用，但灵活性和可控性不够。Nova 是底层运行时，我们有完全的代码控制权，可以根据研发需求任意定制。"

### 6.3 回答"为什么不用现成的"

> "我们也希望用现成的。但现成的框架要么太通用不懂生命科学，要么绑定单一模型，要么不支持本地部署。自研 Nova 短期看是投入，长期看是我们无法被模仿的研发壁垒。"

---

## 七、风险提示

与这些成熟框架相比，Nova 的明显劣势：

1. **成熟度**：LangChain、Kimi、Codex 有更长时间的生产验证。
2. **生态规模**：社区、第三方集成、文档都不如主流框架。
3. **多 Agent 编排**：AutoGen、CrewAI、LangGraph 在多 Agent 协作方面更成熟，Nova 的 `nova_team` 仍在 WIP。
4. **人才市场**：熟悉 LangChain 的开发者更多，Nova 需要内部培养。

**应对话术**：

> "Nova 当前确实不如 LangChain 成熟，但我们的目标不是做一个通用框架，而是做一个能完美服务我们生物研发场景的运行时。成熟度可以通过投入追赶，但架构上的适配性很难通过后发模仿实现。"

---

## 八、总结

Nova 的核心竞争策略不是"比所有框架都好"，而是：

> **在"企业级科学计算 Agent 运行时"这个细分赛道建立不可替代性。**

这个赛道的关键成功因素是：

1. **可控性** — 自研运行时
2. **场景适配** — 场景化压缩、垂直 bundle
3. **合规性** — 模型中立、本地部署
4. **可扩展性** — 事件驱动、Bundle 化、多前端

LangChain 们做的是"通用工具箱"，Kimi/Codex 做的是"模型应用"，Dify 做的是"应用平台"，而 Nova 做的是**"属于我们自己的、为生物研发优化的 Agent 操作系统"**。
