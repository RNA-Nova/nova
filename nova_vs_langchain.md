# Nova 框架与 LangChain 对比分析

> 本文档从架构定位、核心抽象、扩展机制、多智能体支持、开发体验与适用场景等维度，对 Nova 与 LangChain 进行对比分析，帮助开发者根据项目需求选择合适的框架。

---

## 1. 概述

| 维度 | Nova | LangChain |
|------|------|-----------|
| **定位** | 自研 LLM Agent 构建框架，强调分层、事件驱动、可组合的 monorepo 架构 | 主流 LLM 应用编排框架，强调模块化组件与广泛的第三方集成 |
| **语言** | Python（`>=3.9,<3.13`），前端 TUI 使用 TypeScript/Node.js | Python、JavaScript/TypeScript、Java（LangChain4j）等 |
| **成熟度** | Alpha（`0.1.0`），`nova-coding-agent` 为 `1.0.0` | 2018 年起源，2022 年发布，生态成熟，GitHub 116k+ stars |
| **许可证** | MIT | MIT |
| **包管理** | Pixi workspace + Poetry path 依赖 | PyPI / npm 标准包管理 |

---

## 2. 架构设计对比

### 2.1 Nova：严格分层的 Monorepo

Nova 采用**自下而上的分层架构**，将不同职责拆分为独立子包，依赖关系清晰：

```
nova_ai          ← LLM 提供商抽象层（统一流式调用、模型注册表）
  ↑
nova_agent       ← 事件驱动的 Agent 核心框架（Agent 类、agent_loop、工具执行）
  ↑
nova_harness     ← 高阶 SDK（会话树、压缩、持久化、JSON-RPC、Project Trust）
  ↑
nova_coding_agent ← 官方 bundle（coding agent + 本地工具 + session_commands 扩展）
```

- **设计哲学**：每一层只依赖下层，上层为下层提供场景化封装。
- **通信方式**：TUI 前端 `nova-tui` 通过 **JSON-RPC over stdio** 与 `nova_harness` 通信。
- **部署形态**：可作为库嵌入、命令行工具或 RPC 服务器运行。

### 2.2 LangChain：组件化的编排框架

LangChain 以**可组合的组件**（components）为核心，将 LLM 应用拆分为：

- **Model I/O**：Prompts、Models、Output Parsers
- **Retrieval**：Document Loaders、Text Splitters、Vector Stores、Retrievers
- **Chains**：将多个组件组合成流水线
- **Agents**：基于 ReAct、Plan-and-Execute 等策略的自主决策循环
- **Memory**：对话状态管理

从 LangGraph 开始，LangChain 进一步提供**图状态机**（state machine）编排能力，支持：

- 分支、循环、并行执行
- 持久化检查点（checkpointing）
- Human-in-the-Loop 与 Time-Travel 调试

### 2.3 架构差异总结

| 方面 | Nova | LangChain |
|------|------|-----------|
| 组织方式 | Monorepo 分层子包 | 多仓库 / 多语言生态 |
| 抽象层次 | 中低层，强调 Agent 运行时与会话管理 | 高层，强调组件组合与快速集成 |
| 状态管理 | 会话树（分支/fork/导航）、上下文压缩 | Memory、LangGraph 状态图 |
| 通信协议 | JSON-RPC over stdio | 无内置 RPC，多为库内调用或 HTTP API |
| 多语言 | 以 Python 为主，TUI 用 TS | Python / JS / Java 均有官方支持 |

---

## 3. 核心抽象对比

### 3.1 Nova 的核心抽象

Nova 的核心抽象围绕**事件驱动的 Agent 生命周期**：

- **`Agent`**（`nova_agent/agent.py`）：状态管理、事件订阅、消息队列、生命周期。
- **`agent_loop`**（`nova_agent/agent_loop/`）：异步循环，负责工具执行、模型调用、事件发布。
- **`AgentSession`**（`nova_harness/core/agent_session/`）：运行时核心，包含树、队列、工具、压缩等控制器。
- **`ToolDefinition` / `ToolExecutor`**：工具定义与执行分离，工具以 bundle 形式通过 `nova-pkg` 安装。
- **`ExtensionUIContext`**：UI 桥接，支持前后端通过事件交互。

Nova 强调：

- **Pydantic v2** 用于 JSON/文件/RPC 边界类型。
- **dataclass** 用于运行时内部对象（如事件 payload、服务容器）。
- **asyncio** 作为唯一异步运行时。

### 3.2 LangChain 的核心抽象

LangChain 的核心抽象围绕**链式组合**：

- **`Runnable`**：统一的可执行接口，支持流式、批处理、异步。
- **`Chain`**：组件的线性组合。
- **`AgentExecutor`**：执行 Agent 策略的循环。
- **`Tool`**：函数式工具，通过装饰器定义。
- **`BaseChatModel` / `LLM`**：模型接口抽象。
- **`LangGraph`**：以节点-边图结构编排多步骤工作流。

LangChain 提供极高的**即插即用性**，但也因此抽象层次较深。

### 3.3 核心抽象差异总结

| 方面 | Nova | LangChain |
|------|------|-----------|
| 核心单元 | Agent + 事件 + 会话 | Component + Chain + Runnable |
| 循环控制 | 自研 `agent_loop`，显式事件驱动 | `AgentExecutor` / LangGraph 状态机 |
| 工具定义 | 通过 `schema.json` + `executor.py` bundle 化 | 函数装饰器或 `BaseTool` 子类 |
| 模型抽象 | `nova_ai` 统一多厂商适配，当前仅 OpenAI 实现完整 | 支持 OpenAI、Anthropic、Google、HuggingFace 等大量后端 |
| 序列化策略 | Pydantic 边界 + dataclass 内部 | 大量使用 Pydantic，但内部也以对象组合为主 |

---

## 4. 工具与扩展性对比

### 4.1 Nova 的工具与扩展

Nova 的工具和扩展是**一等公民**，通过包管理器 `nova-pkg` 安装：

- **工具 bundle**：每个工具包含 `schema.json` + `executor.py`，如 `bash`、`edit`、`find`、`grep`、`ls`、`read`、`write`。
- **扩展系统**：`nova_harness/core/extensions/` 提供扩展 API、loader、runner、wrapper。
- **Agent bundle**：如 `coding_agent`，通过 `agent.yaml` 声明元数据、工具白名单、扩展白名单。
- **子包扩展**：官方提供 `subagent` 扩展。

示例：`nova_coding_agent` 的 `pyproject.toml` 中 `[tool.nova]` 段声明了 agents、tools、extensions，支持 `auto_install_dependencies` 与 `binary_dependencies`。

### 4.2 LangChain 的工具与扩展

LangChain 的工具生态是其最大优势之一：

- **`@tool` 装饰器**：快速将 Python 函数暴露为 Agent 工具。
- **langchain-community**：数百个第三方集成（搜索、数据库、向量存储、API 等）。
- **LangChain Templates / Hub**：预置提示词与链模板。
- **可扩展性**：通过继承 `BaseTool`、`BaseRetriever`、`BaseChatModel` 等扩展。

### 4.3 扩展性差异总结

| 方面 | Nova | LangChain |
|------|------|-----------|
| 工具数量 | 目前较少，官方提供 7 个本地工具 | 极其丰富，社区贡献数百个集成 |
| 安装方式 | `nova-pkg install <path>` 安装本地/远程 bundle | `pip install langchain-xxx` |
| 扩展粒度 | Agent、Tool、Extension、Skill 独立打包 | Chain、Tool、Retriever、Model 等组件自由组合 |
| 自定义成本 | 需遵循 bundle 目录结构与 manifest 规范 | 低，函数装饰器即可定义工具 |

---

## 5. 多智能体支持对比

### 5.1 Nova 的多智能体

Nova 提供 **`nova_team`** 子包（早期 WIP），支持：

- **`TeamDefinitor`**：动态合并配置、状态修改与保存。
- **两级存储**：项目级与全局级存储后端（文件 + 内存）。
- **主从挂载配置**：`SubagentMountEntry`、`MasterMountEntry`。

目前 `nova_team` **尚未配置 `pyproject.toml`**，不可独立安装，属于早期实现。

### 5.2 LangChain 的多智能体

LangChain 通过 **LangGraph** 提供成熟的多智能体编排：

- 将工作流建模为**状态图**（state graph）。
- 支持**持久化检查点**、循环、分支、并行。
- 支持 Human-in-the-Loop 干预。
- 支持 Time-Travel 调试，可回溯状态。

LangGraph 被定位为"用于构建长期运行、有状态 Agent 的低级编排框架和运行时"。

### 5.3 多智能体差异总结

| 方面 | Nova | LangChain |
|------|------|-----------|
| 实现状态 | 早期 WIP（`nova_team`） | 成熟（LangGraph） |
| 编排模型 | 主从挂载 + 配置驱动 | 状态图 + 节点/边 |
| 持久化 | 会话 JSONL + 信任记录 | LangGraph checkpointing |
| 调试能力 | 基础会话树导航 | Time-Travel、Human-in-the-Loop |

---

## 6. 开发体验对比

### 6.1 Nova 的开发体验

**优势：**

- 架构清晰，分层职责明确，便于深入定制。
- 事件驱动模型使 Agent 行为易于观测与干预。
- 内置 TUI（`nova-tui`）提供终端交互界面。
- 中文注释与文档，对中文开发者友好。
- Project Trust 提供项目级安全门控。

**劣势：**

- Alpha 阶段，API 可能不稳定，生态较小。
- 当前仅 `nova_ai` 的 OpenAI 实现完整，其他厂商适配待完善。
- `nova_harness` 存在若干既有失败用例，测试覆盖仍在完善。
- 学习曲线相对陡峭，需要理解 monorepo 分层。

### 6.2 LangChain 的开发体验

**优势：**

- 生态极其丰富，集成数量远超 Nova。
- 文档与社区成熟，示例众多。
- 快速原型能力强，几十行代码即可实现 RAG 或 Agent。
- LangGraph 提供强大的状态化多智能体编排。
- 多语言支持（Python / JS / Java）。

**劣势：**

- 抽象层次深，约 800+ 类，学习曲线陡峭。
- 版本迭代快，API 频繁出现破坏性变更。
- 过度封装可能带来性能开销与调试困难。
- 对于非标准需求，绕过抽象的成本较高。

### 6.3 开发体验差异总结

| 方面 | Nova | LangChain |
|------|------|-----------|
| 上手速度 | 较慢，需要理解分层与事件模型 | 快，社区示例丰富 |
| 定制自由度 | 高，代码库自研可控 | 中，受限于框架抽象 |
| 调试友好性 | 事件流 + 会话树 | LangGraph 状态图 + checkpoint |
| 文档语言 | 中文为主 | 英文为主 |
| 社区规模 | 小，早期项目 | 大，成熟生态 |

---

## 7. 安全与治理对比

### 7.1 Nova

- **Project Trust**：`~/.nova/agent/trust.json` 保存项目信任决策，扩展可参与裁决。
- **鉴权隔离**：`nova_ai` 不持久化密钥，`nova_harness` 通过 `AuthStorage` 管理 `~/.nova/agent/auth.json`。
- **会话明文**：会话以 JSONL 明文存储，需注意敏感信息。
- **路径校验**：Agent 配置加载器目前对 `..` 与绝对路径校验有限，生产环境需补充。

### 7.2 LangChain

- **提示注入风险**：模板插值是已知攻击面，框架提供 `escape` 与 `validate_template` 但无法完全防御。
- **密钥管理**：依赖开发者自行管理 API Key，通常通过环境变量。
- **审计能力**：LangGraph 的状态图与 checkpoint 提供较好的执行轨迹审计。

---

## 8. 适用场景建议

### 选择 Nova 的场景

- 你希望构建一个**自研、可控、可深度定制**的 Agent 平台。
- 你需要**事件驱动**的 Agent 运行时，便于观测、干预和扩展。
- 你需要**终端原生**的交互体验（TUI + JSON-RPC）。
- 你希望工具、扩展、Agent 以**bundle 包**形式进行生命周期管理。
- 你的团队偏好中文文档，且愿意参与早期项目共建。
- 你需要将 Agent 框架作为**产品底座**长期演进，而非一次性脚本。

### 选择 LangChain 的场景

- 你需要**快速验证**一个 LLM 应用原型（RAG、Chatbot、Agent）。
- 你需要连接大量**第三方服务**（向量库、搜索引擎、数据库、API）。
- 你需要**成熟的多智能体编排**（LangGraph）与持久化执行。
- 你的团队已经熟悉 LangChain 生态，希望复用现有集成。
- 你需要**多语言支持**（Python + JS/Java）。

### 不推荐 LangChain 的场景

- 工作流非常简单，直接调用 OpenAI/Anthropic SDK 更轻量。
- 对抽象 overhead 敏感，需要完全掌控每一步执行。
- 对 API 稳定性要求高，难以频繁跟进版本升级。

### 不推荐 Nova 的场景

- 需要生产级稳定性与成熟生态支持。
- 需要大量第三方模型/工具集成（当前生态有限）。
- 团队无法接受 Alpha 阶段的迭代风险。

---

## 9. 总结

Nova 与 LangChain 代表了两种不同的 Agent 框架设计哲学：

- **Nova** 是"自底向上"构建的框架，强调分层、事件驱动、包管理与终端原生体验。它更适合希望将 Agent 能力作为产品核心底座、需要深度定制的团队。
- **LangChain** 是"自上而下"的编排框架，强调组件组合、生态集成与快速原型。它更适合需要快速落地、依赖丰富第三方集成的项目。

如果你的目标是**探索、实验、快速上线**，LangChain 是更稳妥的选择。如果你的目标是**构建一个长期演进、结构清晰、可深度控制的 Agent 平台**，Nova 的设计思路更值得参考。

---

## 参考来源

- Nova 项目文档：`AGENTS.md`、`README.md` 及各子包 `pyproject.toml`
- LangChain 官方文档：[LangGraph Overview](https://docs.langchain.com/oss/python/langgraph/overview)
- 学术研究：[An Empirical Study of Agent Developer Practices in AI Agent Frameworks](https://arxiv.org/html/2512.01939v1)
- 行业分析：[Why Are Developers Quitting LangChain?](https://www.upgrad.com/blog/why-are-developers-quitting-langchain/)
- 架构指南：[LangGraph Multi-Agent Orchestration](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/langgraph-multi-agent-orchestration-complete-framework-analysis-2025)
