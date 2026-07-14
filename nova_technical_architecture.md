# Nova 技术架构完整文档

> 本文档全面整理 Nova 框架的技术架构，面向投资人路演、技术尽调和团队内部参考。

---

## 目录

1. [项目定位](#一项目定位)
2. [总体架构](#二总体架构)
3. [第 1 层：nova_ai 模型抽象层](#三第-1-层nova_ai-模型抽象层)
4. [第 2 层：nova_agent Agent 运行时层](#四第-2-层nova_agent-agent-运行时层)
5. [第 3 层：nova_harness 高阶 SDK 层](#五第-3-层nova_harness-高阶-sdk-层)
6. [第 4 层：Agent Bundle 场景应用层](#六第-4-层agent-bundle-场景应用层)
7. [核心机制详解](#七核心机制详解)
8. [多前端生态](#八多前端生态)
9. [未来能力路线图](#九未来能力路线图)
10. [与竞品对比](#十与竞品对比)
11. [生物应用场景](#十一生物应用场景)
12. [投资人 / 技术专家 FAQ](#十二投资人--技术专家-faq)
13. [总结](#十三总结)

---

## 一、项目定位

**Nova 是一家生物科技公司自研的 AI Agent 研发引擎。**

它不是对外销售的软件产品，而是用于加速内部生物医药研发的基础设施。通过 Nova，公司能够：

- 更快地完成靶点调研
- 更高自动化地运行实验
- 更高效地执行生物信息学分析
- 沉淀研发 know-how，形成竞争壁垒

**核心设计哲学**：

> 分层清晰、职责单一、模型中立、事件驱动、场景感知、可扩展可定制。

---

## 二、总体架构

### 2.1 四层架构

```
┌───────────────────────────────────────────────┐
│  第 4 层：Agent Bundle（场景应用层）            │
│  coding_agent / 靶点调研 / 自动化实验 / 生物信息 │
├───────────────────────────────────────────────┤
│  第 3 层：nova_harness（高阶 SDK 层）           │
│  AgentSession · 会话树 · 上下文压缩 · nova-pkg │
│  Project Trust · JSON-RPC / WebSocket 接口     │
├───────────────────────────────────────────────┤
│  第 2 层：nova_agent（Agent 运行时层）          │
│  Agent 类 · agent_loop · 工具执行 · 事件系统   │
├───────────────────────────────────────────────┤
│  第 1 层：nova_ai（模型抽象层）                 │
│  ApiRegistry · ModelRegistry · 统一流式调用    │
└───────────────────────────────────────────────┘
```

### 2.2 前后端架构

```
飞书 Bot ──┐
钉钉 Bot ──┤
企业微信 ──┼──→ WebSocket ──→ nova_harness ──→ Agent Runtime
Web UI ────┤
TUI ───────┘
```

后端通过 WebSocket 与前端解耦，所有前端共享同一套 Agent 运行时、事件日志和安全策略。

### 2.3 设计原则

| 原则 | 说明 |
|------|------|
| **分层解耦** | 每层只依赖下层，底层变化不影响上层业务 |
| **模型中立** | 不绑定单一模型厂商，支持国产/本地/海外模型 |
| **事件驱动** | Agent 的每个关键环节都发出事件，可观测可干预 |
| **场景感知** | 压缩策略、工具集、提示词可按任务类型定制 |
| **Bundle 化** | Agent、工具、扩展可打包、分发、复用 |
| **本地优先** | 支持私有化部署，敏感数据不出内网 |

---

## 三、第 1 层：nova_ai 模型抽象层

### 3.1 设计目标

屏蔽不同大模型 API 的差异，让上层只关心"用哪个模型"，不关心"怎么调 API"。

### 3.2 核心组件

| 组件 | 文件位置 | 职责 |
|------|---------|------|
| **ApiRegistry** | `nova_ai/registry/api_registry.py` | 管理不同 API 协议的适配器 |
| **ModelRegistry** | `nova_ai/registry/model_registry.py` | 管理模型静态数据与动态配置 |
| **统一调用接口** | `nova_ai/streaming/invoke.py` | 提供 `stream()` / `complete()` 等统一调用方式 |
| **消息类型系统** | `nova_ai/types/messages.py` | 定义 Context、Message、AssistantMessage 等统一消息类型 |
| **流式事件** | `nova_ai/streaming/event_stream.py` | 将模型响应转换为统一事件流 |
| **OpenAI 适配器** | `nova_ai/api_impls/openai_completions.py` | 当前最完整的 API 实现 |
| **Volcengine 数据** | `nova_ai/models/volcengine.py` | 国产模型静态数据准备 |

### 3.3 调用流程

```
上层调用 stream(model, context, options)
            ↓
    ApiRegistry 根据 model.api 找到对应适配器
            ↓
    适配器处理鉴权、请求构造、响应解析
            ↓
    返回统一的事件流 AssistantMessageEventStream
```

### 3.4 技术亮点

1. **适配器模式**
   - 每个模型厂商实现 `ApiAdapter` Protocol。
   - 新增模型只需新增适配器，上层代码零改动。

2. **模型即配置**
   - 模型以 `Model` 对象描述：provider、model id、context window 等。
   - 支持运行时切换模型，实现模型路由与降级。

3. **统一消息格式**
   - 无论底层是 OpenAI、Anthropic 还是国产模型，上层使用统一的消息类型。

4. **国产模型与本地模型友好**
   - 已预留 Volcengine 等国产模型数据结构。
   - 抽象层设计支持 vLLM、Ollama、Xinference 等本地推理服务。

### 3.5 对生物研发的价值

- **避免绑定**：不被 OpenAI/Moonshot 等单一厂商锁定。
- **成本控制**：可选用更便宜的国产模型或本地模型。
- **数据合规**：本地部署时生物数据不出内网。

---

## 四、第 2 层：nova_agent Agent 运行时层

### 4.1 设计目标

让 Agent 真正"动起来"：接收输入、调用模型、执行工具、管理状态、发布事件。

### 4.2 核心组件

| 组件 | 文件位置 | 职责 |
|------|---------|------|
| **Agent 类** | `nova_agent/agent.py` | 状态管理、事件订阅、消息队列、生命周期 |
| **agent_loop** | `nova_agent/agent_loop/loop.py` | Agent 主循环实现 |
| **工具执行** | `nova_agent/agent_loop/tools.py` | 工具参数校验、调用、Hook 处理 |
| **事件类型** | `nova_agent/types/events.py` | 定义完整事件体系 |
| **工具类型** | `nova_agent/types/tool.py` | 定义 AgentTool、AgentToolCall 等 |
| **AbortSignal** | `nova_agent/signal.py` | 支持任务取消 |
| **参数校验** | `nova_agent/utils.py` | 基于 jsonschema 校验工具参数 |

### 4.3 agent_loop 工作流程

```
用户输入 / 系统消息
        ↓
AgentStartEvent
        ↓
TurnStartEvent
        ↓
调用 LLM 生成响应（流式）
        ↓
检查响应中是否包含 tool calls
    ├─ 没有 → 返回响应，进入停止判断
    └─ 有 → 进入工具执行阶段
            ↓
    ToolExecutionStartEvent
            ↓
    参数校验（jsonschema）
            ↓
    before_tool_call Hook
            ↓
    执行工具（并行/串行）
            ↓
    after_tool_call Hook
            ↓
    ToolExecutionEndEvent
            ↓
    工具结果写回上下文
            ↓
    再次调用 LLM
            ↓
    循环直到无 tool calls 或满足停止条件
        ↓
TurnEndEvent
        ↓
AgentEndEvent
```

### 4.4 工具调用机制

#### 4.4.1 工具定义

每个工具通过 `AgentTool` 描述：

- `name`：工具名称
- `description`：工具功能描述
- `parameters`：jsonschema 参数定义
- `execute`：执行函数
- `execution_mode`：并行或串行

#### 4.4.2 工具调用流程

1. **模型决定调用**：LLM 根据上下文输出 tool calls。
2. **参数校验**：`nova_agent` 用 jsonschema 校验参数类型和约束。
3. **Hook 机制**：
   - `before_tool_call`：执行前拦截、修改参数或拒绝执行。
   - `after_tool_call`：执行后修改结果、记录日志。
4. **执行模式**：
   - **并行**：独立工具同时执行。
   - **串行**：有依赖关系的工具按顺序执行。
5. **结果回传**：工具结果以 `toolResult` 消息写回上下文。

#### 4.4.3 安全控制

- 工具必须显式注册到 AgentContext。
- 参数不符合 schema 会报错，不会执行。
- Hook 可以实现审批、审计、权限控制。

### 4.5 事件系统

事件驱动是 Nova 区别于 LangChain 等链式框架的关键设计。

#### 4.5.1 主要事件

| 事件 | 触发时机 |
|------|---------|
| `AgentStartEvent` | Agent 开始运行 |
| `AgentEndEvent` | Agent 结束运行 |
| `TurnStartEvent` | 新一轮对话开始 |
| `TurnEndEvent` | 一轮对话结束 |
| `MessageStartEvent` | 消息开始生成 |
| `MessageUpdateEvent` | 消息流式更新 |
| `MessageEndEvent` | 消息生成结束 |
| `ToolExecutionStartEvent` | 工具开始执行 |
| `ToolExecutionUpdateEvent` | 工具执行中更新 |
| `ToolExecutionEndEvent` | 工具执行结束 |

#### 4.5.2 事件驱动的价值

1. **可观测**：外部系统可以实时看到 Agent 在做什么。
2. **可干预**：可以在工具执行前拦截或修改。
3. **可审计**：完整记录 Agent 的决策路径。
4. **可扩展**：UI、日志、安全策略都可以通过订阅事件实现。

### 4.6 对生物研发的价值

- **透明可控**：实验设备调用、代码执行都可以被监控和拦截。
- **灵活干预**：当 Agent 要走偏时，可以及时纠正。
- **合规审计**：满足生物医药行业的审计要求。

---

## 五、第 3 层：nova_harness 高阶 SDK 层

### 5.1 设计目标

把 `nova_agent` 的核心能力封装成可长期运行、可管理、可扩展的研发工作空间。

### 5.2 核心组件

| 组件 | 文件位置 | 职责 |
|------|---------|------|
| **AgentSession** | `nova_harness/core/agent_session/agent.py` | 会话运行时核心 |
| **Runtime** | `nova_harness/core/agent_session/runtime.py` | 会话运行时环境 |
| **Tree Navigator** | `nova_harness/core/agent_session/controllers/tree.py` | 会话树导航与分支摘要 |
| **Compaction Controller** | `nova_harness/core/agent_session/controllers/compaction.py` | 上下文压缩控制 |
| **Tools Controller** | `nova_harness/core/agent_session/controllers/tools.py` | 工具管理 |
| **Events Controller** | `nova_harness/core/agent_session/controllers/events.py` | 事件管理 |
| **Model Controller** | `nova_harness/core/agent_session/controllers/model.py` | 模型管理 |
| **Package Manager** | `nova_harness/core/package/` | Agent/Tool/Extension 包管理 |
| **Extension System** | `nova_harness/core/extensions/` | 扩展机制 |
| **Project Trust** | `nova_harness/core/harness/` | 项目级安全门控 |

### 5.3 会话树（Conversation Tree）

#### 5.3.1 为什么需要？

传统对话是线性的：一句接一句，错了只能重开。生物研发需要探索性工作流，一个假设可能产生多个分支。

#### 5.3.2 能力

- **分支（Branch）**：从任意节点创建新的探索分支。
- **Fork**：复制一条完整对话路径进行独立实验。
- **导航（Navigate）**：回到历史任意节点继续。
- **摘要（Summary）**：生成分支摘要，帮助回溯。

#### 5.3.3 结构示例

```
用户提问 A
    ├── AI 回答 B → 分支 1：验证假设 X
    │       └── 工具结果 C
    ├── AI 回答 D → 分支 2：验证假设 Y
    └── 用户修改 → 分支 3：调整参数后重试
```

#### 5.3.4 对生物研发的价值

- **并行探索**：同时验证多个靶点假设。
- **错误回滚**：走错了可以回到任意节点。
- **知识沉淀**：分支摘要成为可复用知识。

### 5.4 上下文压缩（Context Compaction）

#### 5.4.1 为什么需要？

Agent 对话越长，token 消耗指数级增长。生物研发涉及长文献、长序列、长实验日志，必须控制上下文长度。

#### 5.4.2 压缩方式

1. **手动压缩**：用户或系统触发，总结历史对话。
2. **自动压缩**：
   - 达到 token 阈值时触发。
   - 上下文溢出时触发。

#### 5.4.3 压缩流程

```
历史消息
    ↓
保留最近 N 条消息（保留窗口）
    ↓
对更早消息生成摘要
    ↓
用摘要替换旧消息
    ↓
继续对话
```

#### 5.4.4 场景化压缩

不同任务类型采用不同压缩策略：

| 场景 | 保留内容 | 压缩内容 |
|------|---------|---------|
| **Coding** | 代码修改点、关键错误 | 重复编译输出 |
| **文献调研** | 实体关系、结论 | 原始摘要、冗余段落 |
| **实验记录** | 协议、异常事件 | 常规传感器读数 |
| **序列分析** | 关键参数、最终结论 | 中间文件、日志 |

#### 5.4.5 扩展 Hook

压缩过程支持 `SESSION_BEFORE_COMPACT` 扩展事件，允许自定义压缩策略。

### 5.5 nova-pkg 包管理

#### 5.5.1 设计目标

让 Agent、工具、扩展可以像应用一样打包、安装、分发、复用。

#### 5.5.2 Bundle 结构

```
agents/coding_agent/
├── agent.yaml          # Agent 元数据
├── description.md      # 描述文档
└── sections/
    ├── role.md         # 角色定义
    └── setup.md        # setup 说明

tools/bash/
├── schema.json         # 参数 schema
└── executor.py         # 执行逻辑

extensions/session_commands/
└── ...                 # 扩展实现
```

#### 5.5.3 管理能力

- `nova-pkg install <path>`：安装 bundle
- `nova-pkg uninstall <name>`：卸载 bundle
- `nova-pkg update <name>`：更新 bundle
- `nova-pkg list`：列出已安装 bundle
- `nova-pkg validate <path>`：验证 bundle 结构

### 5.6 Project Trust

项目级安全门控机制：

- 当 Agent 首次访问一个项目目录时，需要确认信任级别。
- 扩展可以通过 `project_trust` 事件参与裁决。
- 无 UI 模式默认信任包含 `.nova` 资源的项目。
- 有 UI 时弹出确认对话框。

### 5.7 对生物研发的价值

- **长期会话**：一个靶点可以持续调研数月。
- **成本控制**：场景化压缩显著降低 token 消耗。
- **知识复用**：bundle 化让实验协议、分析流程成为公司资产。
- **安全可控**：Project Trust 和数据本地化满足合规要求。

---

## 六、第 4 层：Agent Bundle 场景应用层

### 6.1 设计目标

把底层框架能力封装成解决具体业务问题的 Agent。

### 6.2 核心价值：让专业的人干专业的事

Bundle 机制最重要的价值是**分离关注点**：

| 角色 | 职责 | 需要懂框架吗？ |
|------|------|---------------|
| 框架工程师 | 维护 nova_ai、nova_agent、nova_harness | 是 |
| 生物信息学家 | 定义靶点分析工具、文献检索逻辑 | 否 |
| 实验专家 | 定义实验协议、设备交互工具 | 否 |
| 数据科学家 | 定义分析 pipeline 和压缩策略 | 否 |

领域专家只需要：

- 用 `agent.yaml` 定义 Agent 元数据
- 用 `schema.json` 定义工具参数
- 用 `executor.py` 实现工具逻辑
- 用 Markdown 编写角色和 setup 说明

**真正懂生物的人能做出更专业的生物 Agent**。

### 6.3 当前 Bundle

| Bundle | 用途 | 状态 |
|--------|------|------|
| **coding_agent** | 编程辅助，验证框架能力 | 已可用 |
| **靶点调研 Agent** | 文献检索、实体抽取、证据汇总 | 开发中 |
| **自动化实验 Agent** | 设备调度、协议执行、异常记录 | 开发中 |
| **生物信息学分析 Agent** | Pipeline 编排、长序列压缩、结果追溯 | 开发中 |

### 6.4 为什么先做 coding_agent？

coding 是验证框架能力的理想场景：

- 工具调用频繁
- 上下文长度典型
- 结果可验证
- 压缩策略效果明显

通过 coding_agent 验证后，再把能力迁移到生物场景。

---

## 七、核心机制详解

### 7.1 从用户输入到 Agent 响应的完整流程

```
1. 用户在前端（TUI / 飞书 / Web）输入请求
            ↓
2. WebSocket 将请求发送到 nova_harness
            ↓
3. nova_harness 创建/恢复 AgentSession，加载对应 Bundle
            ↓
4. nova_agent 启动 agent_loop，发射 AgentStartEvent
            ↓
5. nova_ai 调用配置的 LLM 生成响应
            ↓
6. LLM 决定是否需要调用工具
    ├─ 不需要 → 直接返回文本响应
    └─ 需要 → 输出 tool calls
            ↓
7. nova_agent 校验工具参数
            ↓
8. 执行 before_tool_call Hook
            ↓
9. 并行或串行执行工具
            ↓
10. 执行 after_tool_call Hook
            ↓
11. 工具结果写回上下文
            ↓
12. 再次调用 LLM
            ↓
13. 循环直到任务完成
            ↓
14. 触发上下文压缩（如果需要）
            ↓
15. 更新会话树，保存状态
            ↓
16. 响应通过 WebSocket 返回前端
```

### 7.2 多模型切换与路由

```
业务代码
    ↓
选择 Model 对象（provider + model id）
    ↓
nova_ai 根据 model.api 路由到对应适配器
    ↓
适配器调用具体模型 API
    ↓
返回统一事件流
```

模型切换可以是静态配置，也可以是运行时动态决策（例如成本敏感任务用国产模型，复杂推理任务用 GPT-4）。

### 7.3 工具权限与安全

```
LLM 输出 tool call
    ↓
参数 schema 校验
    ↓
before_tool_call Hook（权限/策略检查）
    ↓
工具执行
    ↓
after_tool_call Hook（审计/结果处理）
    ↓
结果写回上下文
```

Hook 机制允许：

- 拒绝危险操作
- 记录审计日志
- 修改参数或结果
- 触发人工审批

### 7.4 事件流与前端同步

```
Agent 内部事件
    ↓
AgentSession Events Controller
    ↓
WebSocket
    ↓
前端 UI 更新
```

前端可以实时看到：

- Agent 正在思考
- 正在调用哪个工具
- 工具执行进度
- 是否出错
- 最终结果

---

## 八、多前端生态

### 8.1 当前已支持

Nova 后端通过 **WebSocket** 与前端解耦，已支持：

- **TUI（`nova-tui`）**：终端用户界面，适合研究员做复杂分析。
- **Web UI**：可通过 WebSocket 接入。
- **飞书 Bot**：可通过 WebSocket 接入。
- **钉钉 Bot**：可通过 WebSocket 接入。
- **企业微信 Bot**：可通过 WebSocket 接入。

### 8.2 架构优势

```
所有前端
    ↓
WebSocket
    ↓
nova_harness（统一后端）
    ↓
Agent Runtime
```

- 新增前端只需实现协议适配和 UI 形态。
- 所有前端共享同一套 Agent 能力、事件日志、安全策略。
- 不同角色可以在自己熟悉的工具中使用 Agent。

### 8.3 不同角色的使用场景

| 角色 | 前端 | 使用场景 |
|------|------|---------|
| 研究员 | TUI | 复杂分析、长流程任务 |
| 实验员 | 企业微信 | 接收设备异常提醒、确认实验结果 |
| 管理层 | Web | 查看靶点调研报告、实验进度 |
| 项目经理 | 飞书/钉钉 | 接收任务完成通知、审批高风险操作 |

---

## 九、未来能力路线图

### 9.1 Memory（长期记忆）

#### 目标

让 Agent 拥有跨会话的长期记忆，持续积累项目知识。

#### 记忆类型

| 类型 | 存储内容 | 示例 |
|------|---------|------|
| 事实记忆 | 长期不变的知识 | 基因功能、通路结论 |
| 程序记忆 | 工作方式和偏好 | 报告格式、协议版本 |
| 情景记忆 | 历史会话摘要 | 上次调研到一半的靶点 |
| 工作记忆 | 当前会话上下文 | 正在进行的实验任务 |

#### 架构位置

```
AgentSession（当前会话）
        ↑
   Memory Layer
        ↑
   Vector Store / Knowledge Graph / 本地文件
```

### 9.2 Sandbox（安全沙箱）

#### 目标

让 Agent 在受控环境中安全执行代码和调用设备。

#### 安全层级

| 层级 | 机制 |
|------|------|
| 工具白名单 | 只能调用授权工具 |
| 文件系统隔离 | 限制可读写目录 |
| 网络隔离 | 限制网络访问 |
| 执行沙箱 | Docker / 虚拟机中运行代码 |
| 审批流程 | 高风险操作需人工确认 |
| 审计日志 | 完整记录所有操作 |

#### 架构位置

```
用户输入
    ↓
agent_loop
    ↓
安全策略层（Policy Layer）
    ↓
沙箱执行环境
    ↓
实际工具执行
```

---

## 十、与竞品对比

### 10.1 竞品定位

| 产品 | 定位 |
|------|------|
| **Kimi Code CLI** | Moonshot 的终端 coding 工具 |
| **OpenAI Codex CLI** | OpenAI 的终端 coding 工具 |
| **OpenClaw** | 个人 AI 助理框架 |
| **LangChain** | 通用组件化编排框架 |
| **Nova** | 企业级 Agent 运行时基础设施 |

### 10.2 架构对比

| 维度 | Nova | Kimi/Codex | OpenClaw | LangChain |
|------|------|-----------|----------|-----------|
| 架构层次 | 4 层清晰分层 | 终端应用 | Channel+Core+Skill | 组件组合 |
| 模型中立 | ✅ | ❌ 绑定自家 | ✅ | ✅ |
| 运行时 | 自研事件驱动 | 黑盒 | 消息驱动 | Chain/Graph |
| 工具执行 | 校验+Hook+并行/串行 | 内置 | Skill 插件 | Tool 装饰器 |
| 会话管理 | 会话树+压缩 | 线性 | 隔离 | 简单 Memory |
| 可观测性 | 全链路事件 | 有限 | 有限 | 有限 |
| 本地部署 | ✅ | ❌ 弱 | ✅ | ⚠️ 需配置 |
| Bundle 化 | ✅ | ❌ | ⚠️ Skill 偏个人 | ❌ |
| 垂直行业 | ✅ 生物科技 | ❌ coding | ❌ 个人助理 | ⚠️ 需大量定制 |

### 10.3 核心差异总结

- **Kimi/Codex** 是模型公司做的 coding 应用，绑定自家模型。
- **OpenClaw** 是个人助理，连接消息平台。
- **LangChain** 是通用组件框架，抽象层次深。
- **Nova** 是自底向上的企业级 Agent 运行时，强调可控、可观测、场景化、本地部署。

---

## 十一、生物应用场景

### 11.1 靶点调研 Agent

**能力**：

- 自动检索和筛选文献
- 提取靶点、通路、化合物实体关系
- 汇总证据链
- 生成调研报告

**价值**：

- 把数周的文献综述压缩到数天
- 覆盖更全面，减少遗漏
- 结论可追溯、可沉淀

### 11.2 自动化实验 Agent

**能力**：

- 按协议调度实验设备
- 实时监控设备状态
- 自动标记异常事件
- 汇总实验数据与结论

**价值**：

- 减少重复性实验劳动
- 提升实验可重复性
- 24/7 监控设备异常

### 11.3 生物信息学分析 Agent

**能力**：

- 编排基因组、蛋白质组、代谢组分析 pipeline
- 场景化压缩长序列与中间结果
- 保留关键参数与结论
- 让分析流程可复用、可追溯

**价值**：

- 降低模型调用成本
- 减少人工串 pipeline 的错误
- 分析流程成为公司资产

---

## 十二、投资人 / 技术专家 FAQ

### Q1：nova_ai 支持哪些模型？

> 架构上支持任何提供 API 的模型。当前 OpenAI 实现最完整，国产模型和本地模型正在接入。抽象层设计让我们可以快速新增模型适配器。

### Q2：什么是 agent_loop？

> agent_loop 是 Agent 的核心决策循环：模型思考 → 决定调用工具 → 执行工具 → 观察结果 → 再次思考，直到任务完成。

### Q3：工具调用安全吗？

> 我们有参数校验（jsonschema）和 before/after tool call Hook。危险操作可以被拦截、需要审批、记录审计日志。

### Q4：为什么要自研框架，不用 LangChain？

> LangChain 是通用框架，不懂生命科学场景，抽象层次深、版本变化快。我们需要的是场景化压缩、长流程可观测、实验设备可编排、本地部署可控，这些在通用框架上很难优雅实现。

### Q5：场景化压缩和通用压缩有什么区别？

> 通用压缩一视同仁地处理所有上下文，容易丢失关键信息。场景化压缩按任务类型决定保留什么、丢弃什么，比如文献保留实体关系，实验记录保留异常事件。

### Q6：会话树有什么用？

> 传统对话是线性的，错了只能重开。会话树支持分支、fork、导航，适合靶点调研这种需要并行探索多个假设的场景。

### Q7：Bundle 化有什么价值？

> 它让领域专家不需要懂框架底层，也能定义专业 Agent。生物信息学家写靶点分析工具，实验专家写设备协议工具，真正懂生物的人做出更专业的 Agent。

### Q8：多前端生态是真的已经支持了吗？

> 是的。后端通过 WebSocket 解耦，已支持 TUI，并可接入飞书、钉钉、企业微信、Web 等前端。所有前端共享同一套运行时和事件日志。

### Q9：Memory 和 Sandbox 是什么状态？

> 这是路线图上的关键能力，正在规划中。架构上已经预留了扩展点。

### Q10：Nova 是产品还是基础设施？

> 对我们公司而言，Nova 是内部研发基础设施，不是对外销售的产品。它加速靶点调研、自动化实验和生物信息学分析。

---

## 十三、总结

### 13.1 Nova 的四层架构

1. **`nova_ai`**：模型抽象层，屏蔽模型差异，支持国产/本地模型。
2. **`nova_agent`**：Agent 运行时层，自研 agent_loop，事件驱动。
3. **`nova_harness`**：高阶 SDK 层，提供会话树、上下文压缩、包管理、多前端接入。
4. **Agent Bundle**：场景应用层，让领域专家定制专业 Agent。

### 13.2 核心竞争优势

- **模型中立**：不被单一云厂商绑定。
- **事件驱动**：全链路可观测、可干预、可审计。
- **场景感知**：按任务类型定制压缩策略和工具链。
- **Bundle 化**：让专业的人干专业的事。
- **本地优先**：敏感数据不出内网。
- **多前端**：同一套 Agent 能力服务不同角色。

### 13.3 一句话定位

> **Nova 是我们自研的 AI Agent 研发操作系统。它让我们的生物医药研发更快、更省、更可控，同时把领域 know-how 沉淀为竞争壁垒。**
