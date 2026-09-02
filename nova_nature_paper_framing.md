# Nova 在 Nature 论文中的呈现方案（RNA 设计方向）

> 用途：指导如何在以 RNA 设计为主体的 Nature 论文中介绍 Nova（智能体基座层）。
> 核心原则：**Nova 不是主角，是"可复现的智能体基础设施"**——评审对框架细节零兴趣，
> 对"agent 系统为什么可信、可复现"极度敏感。

---

## 1. 定位原则

- 框架出现在 **Methods**（一个简短小节），不进 Abstract、不进标题；
- 不声称"新框架"是论文贡献（否则评审会问"这是工具论文还是科学论文"）；
- 工程细节全部翻译成科学语言；术语（JSON-RPC、pydantic、事件总线）进 SI 或不提；
- 用 "agentic"（智能体式）而非 "autonomous"（自主），并明确人类监督断点。

## 2. 定位句（一句话）

> 本研究的智能体工作流基于自研的开源智能体编排框架 Nova 实现；该框架将语言模型的推理与领域工具的执行解耦，为 RNA 设计任务提供可审计、可复现、可扩展的执行底座。

## 3. 能力点对照表（工程事实 → 科学语言）

| 工程事实 | 论文里的科学语言 |
|---|---|
| 事件溯源（全量会话事件进 JSONL 档案） | **全程可审计**：agent 的每一次推理、工具调用、参数与结果都以不可变事件形式持久化，构成完整的计算实验记录 |
| 会话树（fork/导航/分支摘要） | **设计谱系可溯**：每个 RNA 设计分支对应会话树的一个 fork，任何设计决策点可回溯、可分叉重走 |
| 包管理（工具/技能/agent 定义全部来自可安装包，框架零内置） | **模块化与可复现**：每个生物信息学能力以版本化包形式声明与安装，环境可由包清单精确重建 |
| 多模型 + 契约可插拔（后端可换语言） | **模型无关**：不绑定任何单一 LLM 供应商，结论不依赖特定模型的私有行为 |
| trust 门控 + 工具白名单 | **受控执行**：agent 只能调用显式授权的工具集，防止无界副作用 |

## 4. RNA 设计语境下的叙事框架

把 agent loop 写成科学方法的形式化：

1. **假设 → 工具 → 评估 → 迭代**：agent 将自然语言目标（如"设计一个靶向 X 的核酶"）分解为折叠预测、热力学评分、序列过滤等工具调用序列；每轮结果回灌上下文，驱动下一轮精炼；
2. **工具即包**：RNA 流水线的每个环节是一个独立、版本化的能力包；正文可附"能力包表"（名称/版本/功能）；
3. **人审断点**：user tool 与 trust 机制保证关键决策点有人类确认——在论文语境中这是"人机协同"的正面叙事，不是缺点。

## 5. 形式建议

- **Methods 小节**："Agent orchestration framework"，150–250 词；
- **一张图**：分层架构（模型层 / 智能体运行时 / 能力包层 / RNA 流水线）+ 科学回路（假设→调用→评估→迭代）；
- **Supplementary Information**：架构细节、能力包清单、版本与复现指引（GitHub 链接 + 会话档案样例）。

## 6. Methods 小节草稿

### 中文版（终稿）

> 本研究的智能体系统基于 Nova 构建。Nova 是我们为本类研究自研并开源的智能体开发框架；智能体的具体设计见第 X 节。与主流智能体框架相比，Nova 有两项特性直接服务于计算实验的可信性。其一，会话以树形谱系持久化，任一决策点均可分叉与重放：我们得以在同一记录谱系内对关键决策施加受控扰动——例如更换模型或调整评分阈值后自该点重走——而非仅事后检索日志。其二，安全筛查在决策环内强制执行：一切涉及序列层面的操作均须经环内钩子筛查，命中预设风险模式即被阻断，且该机制无法被智能体绕过。此外，**框架的上下文工程可按领域深度定制**：系统提示词组合、模型调用前的上下文变换、以及长上下文压缩三个环节均可由领域扩展编程接管——使关键设计状态（如序列约束与评分历史）按任务语义被精确保留，而非依赖通用文本摘要。框架源代码与分析模块清单见补充材料及 [仓库链接]。

**终稿打磨注记**：首句断开（"构建。Nova 是……"）避免长定语；"施加受控扰动""自该点重走""涉及序列层面的操作""无法被绕过"按学术语感收紧；破折号仅保留扰动示例一处。

**优点替换注记（v5 → 终稿）**：删去"模型出处戳 + 双模型复现"句（该证据建议挪至结果稳健性小节或智能体设计节，不要浪费——它是数据证据不是框架特性）；换入**定制化上下文工程**。诚实底座：三个接管点今天就在代码里（`SESSION_BEFORE_COMPACT` 扩展可整体接管压缩、`transform_context` 环内上下文变换、分节式系统提示词组合），现在时陈述全部可验；畅想部分（RNA 领域记忆库、语义化压缩保留序列约束）由"智能体的具体设计见第 X 节"承接，形成"框架 → 智能体设计"的递进叙事。

### 英文版（v5）

> The agents used in this study were built on Nova, an open-source framework we developed for agentic systems; the agent designs themselves are described in Section X. Two properties of the framework directly serve the trustworthiness of computational experiments and are absent from mainstream agent frameworks: first, **sessions are persisted as a navigable tree in which any decision point can be branched and replayed**—allowing controlled perturbations (for example, re-running a step with a different model or scoring threshold) within the same recorded lineage, rather than merely inspecting logs after the fact; and second, **safety screening is enforced inside the decision loop**—every action affecting the sequence level passes through an in-loop hook that blocks it upon matching predefined risk patterns, and cannot be bypassed by the agent. In addition, **the framework's context engineering is domain-customizable**: system-prompt composition, pre-invocation context transformation, and long-context compaction can each be programmatically taken over by domain extensions, so that critical design state (for example, sequence constraints and scoring histories) is preserved exactly according to task semantics rather than generic text summarization. Source code and the manifest of analysis modules are available in the Supplementary Information and at [repository link].

**v5 的优点选择与诚实度注记**：

| 写入正文的优点 | 科学语言 | 诚实度 |
|---|---|---|
| 决策点分叉重放 | "同一记录谱系内的受控扰动实验" | LangGraph checkpoint 接近但语义是回滚非谱系对照——措辞用 "rather than merely inspecting logs" 做区分，不声称独有 |
| 环内强制筛查 | "环内钩子可阻断、智能体不可绕过" | 主流框架回调多为 advisory；此措辞经得住核查（pi 有此能力，但 pi 不在科学框架对比面） |
| 模型出处戳 | "机器记录，非模型自述" | 真罕见；一句带过即可 |
| （备选，未入正文）研究者数据回流 | "wet-lab 中间结果注入运行中的设计环" | 留给智能体设计节或 SI——正文塞四条会稀释 |

**风险提示**：v5 的"absent from mainstream agent frameworks"是强主张——投稿前建议在 SI 放一段与 LangGraph/AutoGen 的对照说明（谱系分叉 vs checkpoint 回滚、阻断式 vs advisory 回调），预防评审拿反例挑战。

## 7. 架构图结构描述（供画图）

```
┌─────────────────────────────────────────────┐
│ RNA 设计流水线（折叠预测 / 评分 / 过滤 / 实验队列）│   ← 领域能力层
├─────────────────────────────────────────────┤
│ 能力包层（版本化、可安装的 tools / skills）        │   ← 每个环节一个包
├─────────────────────────────────────────────┤
│ 智能体运行时（agent loop · 会话树 · 压缩 · 白名单） │   ← Nova 核心
│  + 事件溯源档案（JSONL，不可变审计记录）           │
├─────────────────────────────────────────────┤
│ 模型层（LLM，统一契约，供应商无关）                │   ← 可插拔
└─────────────────────────────────────────────┘
回路：自然语言目标 → 假设分解 → 工具调用 → 结果评估 → 迭代精炼
断点：关键步骤人类确认（user tool / trust 门控）
```

## 9. 包发布机制的定位（SI 级优点）

**诚实对照**：能力包管理器本身非独有（pi、Claude Code plugins 有同类）；真正稀缺的是**双生态编排**（一个包同时声明并解析 Python/Node.js/原生二进制依赖）。论文里的价值落点是**可复现性**，不是正文卖点。

**建议**：正文 Methods 只在可用性句加半句；SI 的可复现性小节展开如下：

### SI 草稿：环境与能力复现（中文）

> 本研究使用的全部分析能力（结构预测、热力学评分、序列过滤等）以版本化能力包的形式分发。每个能力包统一声明其 Python 依赖、Node.js 依赖与原生二进制依赖；安装时由框架依次解析——其中原生二进制按锁定版本下载并经受 SHA-256 完整性校验。全部计算环境（含智能体定义、工具、技能与依赖）可由包清单精确重建；每个已安装包的来源与版本均有安装快照（dist-info）记录，可逐包审计。包清单见附表 X。

### SI draft (English)

> All analysis capabilities used in this study (structure prediction, thermodynamic scoring, sequence filtering) are distributed as versioned capability packages. Each package declares its Python, Node.js and native-binary dependencies in a single manifest; installation resolves them in sequence, with native binaries downloaded at pinned versions and verified by SHA-256 checksums. The complete computational environment—including agent definitions, tools, skills and dependencies—can be rebuilt exactly from the package manifest; the source and version of every installed package are recorded in installation snapshots (dist-info) and can be audited per package. The package manifest is provided in Supplementary Table X.

**注意**：SI 这段每句都有代码事实支撑（五阶段安装、pin+sha256 托管注册表、dist-info 快照、每包自含 node_modules），评审查证时全数可验。别写"自动安装一切"——系统级二进制（`binary_system_dependencies`）我们只校验不代装，措辞已按此收。

## 8. 雷区清单（评审视角）

1. **别把正文写成软件文档**：工程名词全进 SI；正文只讲"它保证了什么"；
2. **别吹"全自动"**：用 agentic + 人类断点；autonomous 会被要求证；
3. **别声称框架本身是贡献**：除非数据证明框架带来独特能力（如可复现性本身成为卖点时，可在 Discussion 轻点一句"该框架使全部设计决策可回放"）；
4. **模型无关性要写清楚**：避免"结果依赖某私有模型"的质疑——说明可替换性与结果稳健性；
5. **可复现性要可执行**：包清单 + 会话档案样例 + 仓库链接必须真实可用（这是 Nature 数据/代码可用性政策的硬要求）。
