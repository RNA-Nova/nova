# Nova

Nova 是一个用于构建大语言模型（LLM）智能体的单体仓库（monorepo）：分层架构把
LLM 提供商抽象、Agent 核心框架、高阶 SDK、官方编程 Agent 与通用执行后端拆为
独立子包，按需组合、独立迭代。

## 项目结构

```
nova/
├── packages/
│   ├── nova_ai/            # 统一的 LLM 提供商抽象层（多厂商流式调用/注册表/鉴权）
│   ├── nova_agent/         # 事件驱动的异步 Agent 框架（Agent 类/agent_loop/事件）
│   ├── nova-harness/       # 高阶 Agent SDK——backend/（AgentSession、会话树、压缩、
│   │                       #   工具链、RPC 服务器、Project Trust）+ frontend/
│   │                       #   （nova-client：TS 运行时 + 内置 TUI 宿主）
│   ├── nova_coding_agent/  # 官方编程 Agent bundle（5 个 agent 组合声明、10 个本地
│   │                       #   工具、8 个扩展——含 executor 执行后端切换）
│   ├── nova_executor/      # 通用执行后端（Rust：进程/文件系统/PTY + 三平台沙箱
│   │                       # + 托管网络沙箱；JSON-RPC over stdio/WS/SSH；
│   │                       # 协议即产品，线上契约见 packages/nova_executor/PROTOCOL.md）
│   ├── nova-executor-client/   # executor 的 Python SDK（薄客户端：双传输 +
│   │                       # TransportPool 控制/数据面分离 + 版本协商）
│   └── nova-agent-rs/      # agent 层 Rust 备件存档（审批引擎/命令判定——
│                           #   executor 不管审批，等客户端层启用）
├── README.md
├── CHANGELOG.md
├── AGENTS.md             # 面向 AI Coding Agent 的项目指南（最详细）
└── LICENSE
```

## 子包简介

- **nova_ai**：多厂商（OpenAI/Anthropic/Google/Volcengine 等）统一的流式调用、
  模型注册表与鉴权链（runtime override → 存储凭据 → 环境变量 → OAuth）。
- **nova_agent**：核心框架。`Agent` 类、事件订阅/发布、`agent_loop` 异步循环、
  生命周期管理、工具校验与执行。
- **nova-harness**：高阶 SDK。`AgentSession`、会话树（分支/fork/导航）、上下文
  压缩、设置持久化、内置工具链、JSON-RPC 服务器、Project Trust 门控；
  前端 `nova-client` 为 TS 厚应用层运行时，TUI 是其中一种宿主形态。
- **nova_coding_agent**：官方编程 Agent bundle。组合声明（coding_agent + scout/
  planner/reviewer/worker 子代理）、bash/read/write/edit 等 10 个本地工具、
  权限门/计划模式/executor 切换等 8 个扩展。
- **nova_executor**：编程无绑定的通用执行后端（fork 自 codex exec-server 并对齐
  其最新版）。不知道 agent/模型/会话概念；进程/文件系统/PTY + 三平台沙箱
  （macOS Seatbelt / Linux bwrap+landlock / Windows restricted token）+ 托管
  网络沙箱 + 大文件流式读写。

## 技术栈

- Python `>=3.12,<3.14`（各子包）+ Rust（executor）+ TypeScript（nova-client）
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
pixi run -e dev test-all        # 全部
pixi run -e dev test-harness    # 单个（test-ai / test-agent / test-coding）

# executor（Rust 侧）
cd packages/nova_executor
cargo build --workspace && cargo test --workspace

# nova-client（TS/TUI 侧）
cd packages/nova-harness/frontend
npm install && npm run build    # 或 npm run tui 直接跑 TUI
```

更详细的开发指南见 [AGENTS.md](AGENTS.md)。

## 许可证

MIT License
