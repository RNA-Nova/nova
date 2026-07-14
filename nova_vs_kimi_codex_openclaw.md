# Nova 与 Kimi Code CLI、Codex CLI、OpenClaw 对比分析

> 本文档面向投资人路演准备，分析 Nova 与当前主流 AI Agent 产品/框架的差异化定位。

---

## 一、市场格局概述

当前 AI Agent 领域大致分为三类玩家：

| 类型 | 代表 | 特点 |
|------|------|------|
| **模型公司自研 Agent 工具** | Kimi Code CLI、OpenAI Codex CLI、Claude Code | 强模型 + 强应用，绑定自家生态 |
| **通用 Agent 框架/运行时** | LangChain、LangGraph、AutoGen、Nova | 提供基础设施，支持构建各类 Agent |
| **个人助理/消息平台 Agent** | OpenClaw | 本地优先、消息平台集成、配置驱动 |

Nova 的定位是**通用 Agent 运行时框架**，但垂直锚定生物科技等科学计算场景。

---

## 二、竞品画像

### 2.1 Kimi Code CLI（Moonshot AI）

- **发布方**：Moonshot AI（月之暗面），北京。
- **形态**：终端 AI coding agent，开源（Apache 2.0），支持 MCP 工具扩展。
- **核心能力**：读/改代码、执行 shell、网页搜索、多步骤任务规划。
- **模型绑定**：深度绑定 Kimi 模型家族（K2.5 / K2.6 / K2.7-Code）。
- **优势**：
  - 模型能力强，Kimi K2.6 在 SWE-Bench 等 coding 基准上表现优秀。
  - 成本低，API 价格显著低于 GPT/Claude。
  - 中文语境优化好。
- **局限**：
  - 主要是**coding 应用**，不是通用框架。
  - 深度绑定 Moonshot 模型生态，模型层不可替换。
  - 不适合非 coding 的垂直行业场景（如生物实验、靶点调研）。

### 2.2 OpenAI Codex CLI

- **发布方**：OpenAI。
- **形态**：终端 coding agent，Rust 构建，开源（Apache 2.0），86k+ GitHub stars。
- **核心能力**：read / edit / run code，支持 subagent 并行任务，Auto-review，与 GitHub/Slack/Linear/MCP 深度集成。
- **模型绑定**：深度绑定 OpenAI GPT 系列（Codex CLI + GPT-5.5 在 Terminal-Bench 达 83.4%）。
- **优势**：
  - 模型能力顶级，多 Agent 协作能力强。
  - 生态集成深，开发者体验成熟。
  - 2026 年已支持 Codex Web 云端版。
- **局限**：
  - 仍然是**coding 工具**，不是通用 Agent 基础设施。
  - 强绑定 OpenAI 模型与云服务。
  - 对数据主权敏感的行业（生物科技、金融、政务）不友好。
  - 单 Agent 为主（`AGENTS.md` 主要是上下文文件），多 Agent 能力弱于 Claude Code。

### 2.3 OpenClaw

- **发布方**：Peter Steinberger 等独立开发者社区。
- **形态**：开源 AI assistant 框架/运行时，Node.js 服务，本地优先。
- **核心能力**：连接 WhatsApp/Telegram/Discord/Slack 等消息平台，执行 shell、浏览器、文件操作，Skills 扩展生态。
- **架构**：Channel Layer + Agent Core + Skill Layer，配置驱动（Markdown）。
- **优势**：
  - 本地优先，隐私友好。
  - 消息平台集成丰富，适合个人助理场景。
  - Skills 生态（ClawHub）增长快。
- **局限**：
  - 主要面向**个人助理/消息平台**，不是企业级科学计算场景。
  - TypeScript/Node.js 栈，对 Python 生态（生物信息学、AI 研发）支持有限。
  - 架构偏消息驱动，不适合复杂长流程的科学计算 pipeline。

---

## 三、Nova 的相对优势

| 优势维度 | Nova | 说明 |
|----------|------|------|
| **通用框架 vs 垂直应用** | ✅ | Kimi/Codex 是 coding 应用，Nova 是可构建多种 Agent 的运行时框架。 |
| **模型中立** | ✅ | `nova_ai` 抽象层支持国产模型、OpenAI、Anthropic 及本地私有化部署；Kimi/Codex 深度绑定单一厂商。 |
| **垂直场景扩展** | ✅ | Nova 设计为可扩展至生物信息学、自动化实验、靶点调研等科学计算场景。 |
| **场景化上下文压缩** | ✅ | 按任务类型定制压缩策略，coding 已验证，可向生物场景扩展；竞品多为通用压缩。 |
| **数据主权/本地部署** | ✅ | 支持本地私有化部署，生物数据不出内网；Codex/Kimi 以云端 API 为主。 |
| **事件驱动可观测性** | ✅ | 自研 `agent_loop`，工具调用、状态变更、模型响应都是可观测事件；Codex/Kimi 是黑盒应用。 |
| **Bundle 化生态** | ✅ | `nova-pkg` 支持 Agent/Tool/Extension 的打包、分发与复用；OpenClaw 有 Skills，但偏个人助理。 |
| **中文 + 本土化** | ✅ | 中文文档、国产模型友好、符合国内企业合规需求。 |

---

## 四、Nova 的相对劣势

| 劣势维度 | 说明 |
|----------|------|
| **成熟度** | Kimi/Codex 已有大量用户和生产验证，Nova 仍处于 Alpha。 |
| **模型能力** | Kimi/Codex 背靠顶级模型公司，模型本身能力更强；Nova 是框架，不生产模型。 |
| **生态规模** | Codex 86k+ stars，Kimi Code CLI 6k+ stars，OpenClaw 增长迅猛；Nova 社区尚在早期。 |
| **多 Agent 编排** | Codex 已支持 subagent，Claude Code 多 Agent 能力更强；Nova 的 `nova_team` 仍在 WIP。 |
| **集成深度** | Codex 与 GitHub/Slack/Linear/MCP 深度集成；Nova 目前集成有限。 |
| **产品化程度** | Kimi/Codex 是开箱即用的终端产品；Nova 目前更像基础设施，需要二次开发。 |

---

## 五、对投资人的表达建议

### 5.1 不要直接说"我们比 Kimi/Codex 强"

这是不成立的。Kimi 和 Codex 是**终端应用**，Nova 是**框架/基础设施**，二者不在同一赛道。

### 5.2 正确的差异化叙事

```
Kimi Code CLI / OpenAI Codex CLI 是"AI 编程工具"
                    ↓
   它们解决的是"如何写好代码"这个单点问题
                    ↓
   但它们绑定单一模型、不懂科学场景、数据要上云
                    ↓
   Nova 要造的是"可承载科学计算 Agent 的运行时基础设施"
                    ↓
   在这个基础设施上，coding 只是第一个验证场景
   生物信息学、自动化实验、靶点调研才是主战场
```

### 5.3 一句话定位

> **Kimi 和 Codex 是在别人地基上盖房子，Nova 是在为生物科技这类对可控性、合规性、场景化要求极高的客户打地基。**

或者：

> **Codex 是 OpenAI 的 coding agent；Kimi Code 是 Moonshot 的 coding agent；Nova 是企业可以拥有、可控、可扩展的 Agent 基础设施。**

### 5.4 关于 OpenClaw

OpenClaw 与 Nova 都强调本地优先和可扩展性，但方向不同：

- **OpenClaw**：个人助理，消息平台集成，日常生活/工作流自动化。
- **Nova**：企业级科学计算 Agent，复杂长流程 pipeline，数据主权。

表达时可以说：

> "OpenClaw 做的是个人 AI 助理，我们做的是企业科学计算 Agent 的运行时。"

---

## 六、竞品对比表（可直接放入 PPT）

| 维度 | Nova | Kimi Code CLI | OpenAI Codex CLI | OpenClaw |
|------|------|---------------|------------------|----------|
| **定位** | Agent 运行时框架 | Coding Agent 工具 | Coding Agent 工具 | 个人 AI 助理框架 |
| **绑定模型** | 不绑定 | Kimi 模型 | OpenAI 模型 | 不绑定 |
| **本地部署** | ✅ 支持 | ❌ 弱 | ❌ 弱 | ✅ 本地优先 |
| **垂直行业** | ✅ 生物科技等科学场景 | ❌ 仅 coding | ❌ 仅 coding | ❌ 个人助理 |
| **场景化压缩** | ✅ 按任务类型定制 | ❌ 通用压缩 | ❌ 通用压缩 | ❌ 无 |
| **事件驱动** | ✅ 自研运行时 | ❌ 黑盒 | ❌ 黑盒 | ⚠️ 消息驱动 |
| **多 Agent 编排** | ⚠️ WIP（nova_team） | ⚠️ 有限 | ⚠️ subagent | ✅ 支持 |
| **开源协议** | MIT | Apache 2.0 | Apache 2.0 | MIT |
| **成熟度** | Alpha | 较成熟 | 成熟 | 快速成长期 |

---

## 七、一页 PPT 中的表达建议

如果你只有一页 PPT 要展示 Nova 与这些产品的差异，建议用"赛道不同"来切分：

### 页面布局建议

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   Nova 不是另一个 Coding Agent                              │
│   我们是企业可拥有、可控、可扩展的 Agent 运行时基础设施       │
│                                                             │
├──────────────────────────┬──────────────────────────────────┤
│                          │                                  │
│   市场在做什么            │   Nova 在做什么                   │
│                          │                                  │
│   Kimi Code / Codex CLI   │   自研 Agent 运行时框架           │
│   = 模型公司的 coding 工具 │   · 模型中立                     │
│                          │   · 本地部署友好                 │
│   OpenClaw                │   · 场景化上下文压缩             │
│   = 个人 AI 助理          │   · 事件驱动可观测               │
│                          │   · Bundle 化生态                │
├──────────────────────────┴──────────────────────────────────┤
│                                                             │
│   验证场景：coding │ 主战场：生物信息/自动化实验/靶点调研       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 核心话术

> "Kimi Code 和 Codex 是模型公司做的 coding 工具，它们很强，但它们是'房子'；OpenClaw 是个人 AI 助理，面向消息平台和生活工作流。Nova 要造的是'地基'——一个模型中立、本地部署友好、支持场景化压缩的 Agent 运行时，coding 是我们验证这个地基的第一个场景，生物研发才是我们要建的主楼。"

---

## 八、需要注意的风险

1. **不要低估 Kimi/Codex 的模型优势**：它们的模型能力确实强，Nova 不应在"coding 能力"上直接竞争。
2. **不要夸大 Nova 的成熟度**：Alpha 阶段，多 Agent 编排、生态、集成都在建设中。
3. **不要忽视 OpenClaw 的增长**：如果 OpenClaw 快速进入企业场景，可能成为 Nova 的竞品。
4. **要强调时间窗口**：模型公司做应用有优势，但企业客户最终需要可控、可定制、可本地部署的基础设施，这是 Nova 的机会窗口。

---

## 九、总结

Nova 与 Kimi/Codex/OpenClaw **不在同一赛道**：

- **Kimi/Codex**：强模型 + 强 coding 应用，是"消费级/开发者工具"。
- **OpenClaw**：本地优先个人助理，是"生活/工作流自动化工具"。
- **Nova**：自研 Agent 运行时框架，是"企业级科学计算 Agent 基础设施"。

Nova 的核心机会在于：

> **当企业不想把核心数据交给 OpenAI/Moonshot，当它们需要在本地运行 Agent、按场景定制压缩策略、把生物算法/实验/调研流程产品化时，Nova 是更合理的选择。**

这不是"比 Kimi/Codex 更好"，而是"解决它们不愿解决或解决不了的问题"。

---

## 参考来源

- Moonshot AI Kimi K2.6 / Kimi Code CLI 官方资料与社区评测
- OpenAI Codex CLI 官方文档与 GitHub
- OpenClaw GitHub 仓库与社区指南
- 第三方评测：Morph AI、Context Studios、DevOps.com、AIToolsRecap 等
- 学术论文：NatureBench、SpecBench、HWE-Bench 等 coding agent 基准评测
