# Nova 框架投资人路演指南

> 本指南为面向投资人介绍 Nova 框架时的内容结构与讲述逻辑建议，并附赠可用于生成 PPT 视觉素材的 AI 图像提示词。

---

## 一、投资人视角下的 Nova 定位

投资人并不关心技术细节本身，而是关心：

1. **解决什么问题？**（市场痛点）
2. **为什么是你们能做？**（技术壁垒 / 团队优势）
3. **与现有方案有什么不同？**（差异化竞争）
4. **商业模式是什么？**（怎么赚钱）
5. **现在到什么阶段？**（里程碑 /  traction）
6. **融资用来做什么？**（资金用途）

因此，Nova 的路演应该**以问题开场，以愿景收尾**，中间用架构图和场景 demo 建立信任。

---

## 二、建议的 PPT 结构（10-12 页）

### 第 1 页：封面

- **标题**：Nova —— 面向下一代 AI Agent 的轻量编排框架
- **副标题**：让 Agent 像应用一样被构建、分发与管理
- **视觉**：简洁的科技感 Logo + 抽象网络/节点背景

### 第 2 页：一句话定位

- **核心命题**：
  
  > Nova 是一个开源的 LLM Agent 构建框架，通过分层架构、事件驱动和 bundle 化包管理，帮助开发者快速构建可观测、可扩展、可商业化的智能体应用。

### 第 3 页：市场机会

- AI Agent 是未来 2-5 年最重要的应用形态之一。
- 当前市场痛点：
  - **LangChain 等框架抽象过重**，学习曲线陡峭，难以深度定制。
  - **直接调用 SDK 又过于底层**，重复造轮子。
  - **Agent 应用缺乏统一的分发与管理机制**。
  - **中文开发者在 Agent 基础设施上选择有限**。
- 可引用数据：GitHub Copilot、Cursor、Devin 等 Agent 产品验证市场需求。

### 第 4 页：产品形态

展示 Nova 的 three pillars：

1. **Nova AI**：统一的 LLM 调用抽象层（多厂商兼容）
2. **Nova Agent**：事件驱动的 Agent 运行时
3. **Nova Harness**：高阶 SDK + 包管理器 + TUI 前端

一句话：**从底层模型适配到终端交互界面，Nova 提供端到端的 Agent 开发体验。**

### 第 5 页：架构图（核心页）

建议展示分层架构图：

```
┌─────────────────────────────────────────┐
│           nova-tui (终端 UI)            │
│         JSON-RPC over stdio             │
├─────────────────────────────────────────┤
│  nova_coding_agent  │  其他 Agent Bundle │
│     (官方编程 Agent)  │   (第三方/企业)    │
├─────────────────────────────────────────┤
│        nova_harness（高阶 SDK）          │
│  会话树 · 压缩 · 持久化 · 包管理 · Trust  │
├─────────────────────────────────────────┤
│        nova_agent（Agent 运行时）        │
│     事件驱动 · agent_loop · 工具执行     │
├─────────────────────────────────────────┤
│          nova_ai（模型抽象层）            │
│      多厂商适配 · 流式调用 · 注册表       │
└─────────────────────────────────────────┘
```

强调：**分层清晰、职责单一、可独立演进**。

### 第 6 页：核心差异化

| 维度 | 现有框架（如 LangChain） | Nova |
|------|------------------------|------|
| 架构 | 组件化组合，抽象层次深 | 分层 monorepo，自底向上构建 |
| 运行模型 | 链式/图状态机 | 事件驱动，便于观测与干预 |
| 工具管理 | 函数装饰器，松散的生态 | `nova-pkg` bundle 化包管理 |
| 交互 | 多为库调用或 Web | 原生 TUI + JSON-RPC |
| 语言文化 | 英文生态为主 | 中文文档，中文开发者友好 |

关键信息：**Nova 不是又一个 LangChain，而是为 Agent 的"产品化"和"可管理性"重新设计的框架。**

### 第 7 页：应用场景与 Demo

展示 `nova_coding_agent` 作为首个官方 bundle：

- 本地文件系统工具（read / write / edit / bash / find / grep / ls）
- 终端原生 TUI 交互
- 会话分支、上下文压缩、Project Trust

可延展场景：

- **企业知识库 Agent**
- **DevOps / SRE 自动化 Agent**
- **数据分析与报告生成 Agent**
- **教育 / 客服 / 销售助手**

### 第 8 页：商业模式

建议的商业模式组合：

1. **开源核心 + 商业支持**
   - 核心框架 MIT 开源，建立社区与标准。
   - 企业版提供安全审计、SLA、私有部署支持。

2. **Agent / Tool 市场**
   - `nova-pkg` 作为 Agent bundle 分发平台。
   - 优质 Agent / Skill 可收费订阅或按量计费。

3. **云托管服务**
   - 提供 Nova Cloud：Agent 运行托管、会话持久化、团队协作。

4. **企业定制与咨询**
   - 为金融、法律、制造等行业构建专属 Agent 解决方案。

### 第 9 页：当前进展

- Alpha 版本已发布（`0.1.0`，coding bundle `1.0.0`）。
- 核心子包已成型：`nova_ai`、`nova_agent`、`nova_harness`、`nova_coding_agent`、`nova-tui`。
- 官方 coding agent 工具链与 TUI 前端已可用。
- 测试覆盖：`nova_ai` 158 个用例、`nova_agent` 66 个用例通过。
- 采用 Pixi + Poetry 双包管理，降低开发者接入门槛。

### 第 10 页：竞争格局

- **LangChain**：生态成熟，但复杂、抽象重、版本变化快。
- **CrewAI**：强调角色扮演与多智能体协作。
- **AutoGen**：微软出品，侧重对话式多智能体。
- **OpenAI SDK / Anthropic SDK**：底层，缺少框架级能力。
- **Nova 机会**：在"轻量、可控、中文友好、终端原生"的细分赛道建立差异化。

### 第 11 页：路线图

建议分为三个阶段：

| 阶段 | 时间 | 目标 |
|------|------|------|
| **Phase 1：核心稳定** | 0-6 个月 | 稳定 `nova_harness` API，修复既有失败用例，完善文档 |
| **Phase 2：生态扩展** | 6-12 个月 | 推出 `nova-pkg` 市场，支持更多模型厂商，完善 `nova_team` |
| **Phase 3：商业化** | 12-24 个月 | 推出 Nova Cloud 托管服务，企业版支持，行业解决方案 |

### 第 12 页：融资用途

建议资金分配：

- **40% 研发投入**：核心框架稳定性、多模型适配、LangGraph 级编排能力。
- **25% 生态建设**：文档、示例、社区运营、Agent/Tool 市场。
- **20% 商业化团队**：销售、客户成功、企业定制服务。
- **15% 运营与其他**：法务、品牌、办公。

### 第 13 页：团队与愿景

- 强调团队在 LLM、工程基础设施、开源社区方面的经验。
- **愿景**：
  
  > 让每个人都能构建、分发和管理自己的智能体 —— 从一行代码到一个产品。

### 第 14 页：结尾

- **核心数据**：GitHub stars、下载量、社区人数（如有）。
- **CTA**：欢迎试用、加入社区、联系投资。
- **联系方式与二维码**。

---

## 三、讲述技巧

1. **先讲"为什么"，再讲"是什么"**：投资人需要先看懂问题，再理解解决方案。
2. **用 demo 代替截图**：如果条件允许，现场演示 `nova-tui` 与 coding agent 的交互。
3. **避免过度技术化**：不要深入讲解 asyncio、Pydantic、JSON-RPC 等技术细节。
4. **强调控制权**：Nova 让开发者和企业"拥有自己的 Agent 基础设施"，而不是被某个框架锁定。
5. **展示开源潜力**：MIT License + 中文社区是建立生态的重要抓手。
6. **诚实面对阶段**：Alpha 阶段不是劣势，关键是路线图清晰、执行力强。

---

## 四、AI 图像生成提示词（用于 PPT 视觉素材）

以下提示词适用于 Midjourney、Stable Diffusion、DALL-E 3、Ideogram 等文生图模型。可根据需要调整风格、比例与细节。

### 4.1 封面背景图

```
A minimalist futuristic technology background for a startup pitch deck, 
featuring soft glowing neural network nodes connected by thin luminous lines, 
deep navy blue and electric cyan color palette, abstract geometric layers, 
clean negative space on the left for title text, 
professional, premium, high-tech, 8K, cinematic lighting, 
no text, no logos, no humans.
```

**中文参考翻译**：
极简未来科技感路演封面背景，柔和发光的神经网络节点由纤细光线连接，深海军蓝与电青色配色，抽象几何层次，左侧留有干净的负空间用于标题文字，专业、高端、科技感，8K，电影级光影，无文字、无 Logo、无人物。

### 4.2 架构图概念图

```
A clean isometric layered architecture diagram illustration for an AI agent framework, 
five transparent horizontal layers stacked like a platform, 
soft gradients from dark blue at the bottom to bright cyan at the top, 
digital particles and data streams flowing between layers, 
minimal labels as abstract icons, tech blueprint style, 
white background, vector-like clarity, 8K, no text, no humans.
```

**中文参考翻译**：
简洁的等距分层架构图插画，用于 AI Agent 框架，五层透明水平平台堆叠，从底部深蓝到顶部亮青的柔和渐变，数字粒子与数据流在层间流动，最小化图标式标签，科技蓝图风格，白色背景，矢量级清晰度，8K，无文字、无人物。

### 4.3 智能体/机器人形象

```
A friendly, abstract AI agent character rendered as a glowing geometric bot, 
composed of clean lines and translucent cyan panels, 
floating in a dark gradient space with subtle circuit patterns, 
minimalist and professional, suitable as a mascot for a developer tool, 
no face, no text, 8K, high detail.
```

**中文参考翻译**：
一个友好的抽象 AI Agent 角色，呈现为发光的几何机器人，由简洁线条和半透明青色面板构成，悬浮在带有细微电路图案的深色渐变空间中，极简且专业，适合作为开发者工具的吉祥物，无面部、无文字，8K，高细节。

### 4.4 多智能体协作场景

```
A conceptual illustration of multiple AI agents collaborating in a digital workspace, 
several glowing nodes exchanging data streams in a circular network, 
central hub emitting soft light, dark navy background with cyan and violet accents, 
abstract and clean, conveying intelligence, coordination, and automation, 
no text, no humans, 8K.
```

**中文参考翻译**：
多智能体在数字工作空间中协作的概念插画，多个发光节点在环形网络中交换数据流，中央枢纽散发柔光，深海军蓝背景搭配青色与紫罗兰点缀，抽象而简洁，传达智能、协作与自动化，无文字、无人物，8K。

### 4.5 终端/TUI 界面风格图

```
A stylized terminal user interface mockup for an AI coding agent, 
dark mode with cyan and green accent text, 
left sidebar showing conversation tree, main panel showing code editor and chat stream, 
clean monospace typography, subtle glow effects, 
professional developer tool aesthetic, 
no real code content, no text, 8K.
```

**中文参考翻译**：
AI 编程 Agent 的终端用户界面风格样机，深色模式搭配青色与绿色强调文字，左侧边栏显示对话树，主面板展示代码编辑器与聊天流，简洁等宽字体，微妙发光效果，专业开发者工具美学，无真实代码内容、无文字，8K。

### 4.6 投资人喜欢的"增长曲线"图

```
A clean upward exponential growth curve on a dark tech background, 
abstract data points and glowing grid lines, 
color gradient from deep blue to bright cyan, 
minimalist, no axis labels, conveys momentum and scalability, 
suitable for pitch deck, 8K, no text, no humans.
```

**中文参考翻译**：
深色科技背景上的简洁上升指数增长曲线，抽象数据点与发光网格线，从深蓝到亮青的渐变，极简，无坐标轴标签，传达增长势头与可扩展性，适合路演PPT，8K，无文字、无人物。

---

## 五、总结

面向投资人的 Nova 介绍应聚焦：

- **问题**：Agent 开发框架要么太复杂，要么太底层，缺少"可产品化"的中间层。
- **方案**：Nova 通过分层架构、事件驱动、bundle 包管理和终端原生体验，提供清晰可控的 Agent 基础设施。
- **机会**：在中文开发者生态和 Agent 产品化浪潮中建立先发优势。
- **诉求**：融资用于核心稳定、生态建设和商业化落地。

保持简洁、可视化、故事化，是成功的投资人路演关键。
