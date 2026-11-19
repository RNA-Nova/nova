# Nova

Nova 是一个用于构建大语言模型（LLM）智能体的单体仓库（monorepo）：分层架构把
词汇枢纽、LLM 提供商抽象、Agent 核心框架、高阶 SDK、协议服务层、官方编程
Agent 与通用执行后端拆为独立子包，按需组合、独立迭代。

## 项目结构

```
nova/
├── packages/
│   ├── nova_protocol/        # 词汇枢纽（纯形状零行为：消息/事件/模型/鉴权/取消原语/
│   │                         #   报告词汇——全仓类型的单一正典源）
│   ├── nova_catalog/         # 独立模型目录知识、数据源与生成管线
│   ├── nova_ai/              # 统一的 LLM 提供商抽象层（多厂商流式调用/注册表/鉴权）
│   ├── nova_gateway_protocol/ # Gateway 公开 DTO、SSE 事件、版本与 Schema
│   ├── nova_gateway_client/  # 独立 Gateway 官方 SDK（公开 DTO 事件流，不依赖 nova_ai）
│   ├── nova_gateway/         # Nova 原生模型服务、公共目录站、OpenAI 兼容适配
│   ├── nova_logging/         # 结构化日志上下文、formatter、有界队列与关停排干
│   ├── nova_agent/           # 事件驱动的异步 Agent 框架（Agent 类/agent_loop/事件）
│   ├── nova_harness/         # 高阶 Agent SDK（AgentSession、会话树、压缩、资源加载、
│   │                         #   包管理、RuntimeManager、Project Trust）
│   ├── nova_server/          # JSON-RPC 协议服务层（方法表/事件桥/schema 导出/
│   │                         #   连接与传输/归约/client 家族/headless exec）
│   ├── nova_client/          # TS 前端运行时（nova-client：运行时主体 + 内置 TUI 宿主）
│   ├── nova-exec-server/     # 通用执行后端（Rust：进程/文件系统/PTY + 三平台沙箱
│   │                         #   + 托管网络沙箱；协议即产品，线上契约见
│   │                         #   packages/nova-exec-server/PROTOCOL.md）
│   └── nova_exec_server_client/  # executor 的 Python SDK（薄客户端：双传输 +
│                             #   TransportPool 控制/数据面分离 + 版本协商）
├── bundles/
│   └── nova_coding_agent/    # 官方编程 Agent bundle（5 个 agent 组合声明、10 个本地
│                             #   工具、6 个扩展——含 adjudication 裁决子系统）
├── docs/                     # 建模标准专题文档（架构/数据/协议/报告/测试/安全/开发）
├── README.md
├── CHANGELOG.md
├── AGENTS.md                 # 面向 AI Coding Agent 的项目指南（最详细）
└── LICENSE
```

## 子包简介

- **nova_protocol**：全仓词汇枢纽。LLM 消息/内容/模型/流式事件/枚举/compat/
  auth 词汇、agent 循环事件与消息、取消原语、报告词汇（错误分类/警告事件/装配
  诊断）、secret 清洗——纯形状零行为，纯洁性审计机械执法。
- **nova_catalog**：通用模型目录知识与构建编排；`nova_ai` 和 `nova_gateway`
  各自调用构建结果，自己生成并校验消费方专用产物。
- **nova_ai**：多厂商（OpenAI/Anthropic/Volcengine/Moonshot/Kimi 等）统一的流式
  调用、模型注册表与鉴权链（runtime override → 存储凭据 → 环境变量 → OAuth），
  OpenAI Completions、Anthropic Messages 与 Nova Gateway 协议实现。
- **nova_gateway_protocol / nova_gateway_client / nova_gateway**：独立公开协议、
  官方客户端 SDK 与模型服务端。提供 Nova 原生 SSE、公共目录和 OpenAI 兼容入口；
  下游只传 `provider/id`，不接触上游地址或凭据。
- **nova_logging**：共享 structlog 上下文与 formatter、多 sink/目标级过滤、
  有界 stdlib 队列、flush/超时关停和运行统计；不依赖任何 Nova 业务包。
- **nova_agent**：核心框架。`Agent` 类、事件订阅/发布、`agent_loop` 异步循环、
  生命周期管理、工具校验与执行。
- **nova_harness**：高阶 SDK。`AgentSession`、会话树（分支/fork/导航）、上下文
  压缩、设置持久化、资源加载（agents/tools/skills/prompts/personas）、包管理器、
  Project Trust 门控。
- **nova_server**：JSON-RPC 协议服务层。方法注册表（签名即契约）、事件哑管道、
  线上契约构建期导出（schema + TS）、多连接与背压、反向 UI 原语、服务器侧归约。
- **nova_coding_agent**：官方编程 Agent bundle。组合声明（coding_agent + scout/
  planner/reviewer/worker 子代理）、bash/read/write/edit 等 10 个本地工具、
  adjudication（裁决）/session_commands/plan_mode/tools_panel/interactive_shell/
  executor_switch 共 6 个扩展。
- **nova-exec-server**：编程无绑定的通用执行后端（fork 自 codex exec-server）。
  不知道 agent/模型/会话概念；进程/文件系统/PTY + 三平台沙箱（macOS Seatbelt /
  Linux bwrap+landlock / Windows restricted token）+ 托管网络沙箱 + 大文件流式
  读写。

## 技术栈

- Python `>=3.13,<3.14`（各子包）+ Rust（nova-exec-server）
  + TypeScript（nova-client）
- **pixi** 统一环境管理（根 `pyproject.toml` 的 `[tool.pixi.*]` workspace）；
  各子包保留 Poetry 配置兼容
- 序列化：pydantic v2；异步：asyncio；Rust 侧：tokio + axum
- 格式化：black + isort（Python）/ rustfmt（Rust）

## 安装与开发

```bash
# 安装 pixi（如尚未安装）
curl -fsSL https://pixi.sh/install.sh | bash

# 安装开发环境（含 black/isort/pytest 等）
pixi install --environment dev

# 跑测试（各子包）
pixi run -e dev test-all        # 全部套件
pixi run -e dev test-harness    # 单个（test-ai / test-catalog / test-gateway / test-agent / test-server / ...）

# nova-exec-server（Rust 侧）
cd packages/nova-exec-server
cargo build --workspace && cargo test --workspace

# nova-client（TS/TUI 侧）
cd packages/nova_client
npm install && npm run build    # 或 npm run tui 直接跑 TUI
```

更详细的开发指南见 [AGENTS.md](AGENTS.md)；架构与各包组织见
[docs/architecture.md](docs/architecture.md)。

## 许可证

本仓库采用双许可结构：

- 根目录与其余子包：**MIT License**（见 [LICENSE](LICENSE)）
- `packages/nova-exec-server`（Rust 执行后端）：**Apache License 2.0**——该
  工作区派生自 [OpenAI Codex](https://github.com/openai/codex) 的
  `codex-rs`，按其许可证要求沿用 Apache-2.0（见该目录下的
  [LICENSE](packages/nova-exec-server/LICENSE) 与根目录 [NOTICE](NOTICE)）
