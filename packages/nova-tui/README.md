# Nova TUI

基于 `pi-tui` 的终端用户界面，为 `nova_harness` Python SDK 提供交互式 TUI 前端。

## 架构

```
┌─────────────────────────────────────┐
│         Node.js 主进程               │
│   ┌─────────────────────────────┐   │
│   │      @earendil-works/pi-tui │   │
│   │  ┌─────────┐  ┌──────────┐  │   │
│   │  │Transcript│  │ Editor   │  │   │
│   │  └─────────┘  └──────────┘  │   │
│   └─────────────────────────────┘   │
│              JSON-RPC               │
│              (stdio)                │
└──────────────┬──────────────────────┘
               │
┌──────────────▼──────────────────────┐
│         Python 子进程               │
│   ┌─────────────────────────────┐   │
│   │  nova_harness.rpc    │   │
│   └─────────────────────────────┘   │
│              AgentSession           │
│              nova_harness           │
└─────────────────────────────────────┘
```

- **Node.js 主进程**：负责终端渲染、用户输入、消息展示。
- **Python 子进程**：`nova_harness.rpc` 模块通过 stdio JSON-RPC 暴露 `nova_harness.AgentSession` 的完整能力。
- **通信协议**：JSON-RPC 2.0，行分隔（NDJSON）。

## 目录结构

```
packages/nova-tui/
├── src/
│   └── tui/                   # Node.js TUI 前端
│       ├── main.ts            # CLI 入口
│       ├── app.ts             # NovaTUI 主应用
│       ├── rpc-client.ts      # JSON-RPC 客户端 + 子进程管理
│       ├── state.ts           # TUI 状态定义
│       ├── components/        # pi-tui 组件
│       │   ├── assistant-message.ts
│       │   ├── footer.ts
│       │   ├── status-message.ts
│       │   ├── tool-call.ts
│       │   └── user-message.ts
│       └── controllers/       # 业务控制器
│           ├── editor-keyboard.ts
│           ├── event-handler.ts
│           ├── streaming-ui.ts
│           └── transcript.ts
├── package.json               # npm 依赖（pi-tui, chalk, commander）
├── pyproject.toml             # Poetry 依赖（nova-harness）
├── tsconfig.json
└── README.md
```

## 安装

### 前提条件

- **Python ≥ 3.9**，且已安装以下包（均为 Nova monorepo 子包）：
  - `nova_ai`
  - `nova_agent`
  - `nova_harness`
- **Node.js ≥ 20**（推荐 ≥ 22）

### 1. 安装 Python 依赖

由于 `nova-tui` 依赖 `nova_harness`，而 `nova_harness` 又依赖 `nova_ai` 和 `nova_agent`，需要确保这三个包都在同一 Python 环境中可导入。

**方式 A：使用 pip（推荐，无需 Poetry）**

```bash
cd /path/to/nova-monorepo

# 逐个安装为 editable 模式
pip install -e packages/nova_ai
pip install -e packages/nova_agent      # 源码包名为 nova_agent
pip install -e packages/nova_harness
pip install -e packages/nova-tui
```

**方式 B：使用 Poetry**

```bash
cd packages/nova-tui
poetry install
# Poetry 会自动处理 nova-harness 的 path 依赖
# 但 nova_ai 和 nova_agent 仍需单独安装到同一环境
```

**方式 C：直接用 PYTHONPATH（开发调试）**

```bash
export PYTHONPATH="${PYTHONPATH}:$(pwd)/packages/nova_ai/src:$(pwd)/packages/nova_agent/src:$(pwd)/packages/nova_harness/src"
```

> Python 包的路径由 Python 环境自己管理。Node.js 端启动 Python 子进程时不再自动推断 monorepo 目录。

### 2. 安装 Node.js 依赖

```bash
cd packages/nova-tui
npm install
```

### 3. 构建

```bash
npm run build
```

### 4. 安装为全局命令（可选）

```bash
# 方式 1: npm link（推荐，开发时随时同步修改）
npm link

# 方式 2: 全局安装（发布到 npm 后）
npm install -g nova-tui
```

安装后可直接使用 `nova` 命令：

```bash
nova --help
```

## 用法

```bash
# 启动新会话
nova
nova --dir /path/to/project

# 恢复指定会话
nova -r <session-id>

# 交互式选择会话（TUI 弹窗）
nova -r

# 继续上一个会话
nova -c

# 指定模型
nova -m deepseek-v3

# 组合使用
nova -c -m deepseek-v3 --dir /path/to/project

# 指定 Python 解释器
nova --python /usr/bin/python3
```

## 快捷键

| 按键 | 功能 |
|------|------|
| `Enter` | 发送消息 |
| `Shift + Enter` | 插入换行 |
| `Ctrl + C` | 取消当前流 / 退出 |
| `Ctrl + D` | 退出 TUI |

## JSON-RPC 协议

### Node.js → Python（Request）

| 方法 | 说明 |
|------|------|
| `initialize` | 初始化，返回服务器能力 |
| `createSession` | 创建或恢复 AgentSession（支持 `sessionFlag`、`continueLast`） |
| `listSessions` | 列出当前目录下的所有会话 |
| `prompt` | 发送用户消息 `{ text: string }` |
| `abort` | 中止当前流 |
| `setModel` | 切换模型 `{ model: Model }` |
| `setThinkingLevel` | 设置思考层级 `{ level: string }` |
| `getSessionStats` | 获取会话统计 |
| `getContextUsage` | 获取上下文用量 |
| `newSession` | 新建会话 |
| `dispose` | 清理当前会话 |
| `shutdown` | 关闭服务器 |

### Python → Node.js（Notification）

| 方法 | 说明 |
|------|------|
| `agent/event` | Agent 事件流（message_start / message_update / message_end / tool_execution_start / tool_execution_end / turn_start / turn_end / agent_start / agent_end 等） |

## 开发提示

- Python 子进程的 stderr 会直接透传到 Node.js 的 stderr，便于调试。
- `NovaRpcClient` 启动时只设置 `PYTHONUNBUFFERED=1`，`PYTHONPATH` 继承当前 shell 环境。需要确保 `nova_harness` 及其依赖已通过 `pip install -e` 或正常安装到目标 Python 环境。
- 所有 pi-tui 组件参考了 `kimi-code` 中对 `@earendil-works/pi-tui` 的使用方式。
