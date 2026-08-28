# nova-executor

`nova-executor` 是 Nova 的远程执行服务，从 OpenAI Codex 的 `codex-exec-server` 抽离并独立化。

## 功能

- **进程管理**：通过 JSON-RPC 启动、读取、写入、终止进程
- **PTY 支持**：支持交互式 shell（bash、python REPL 等）
- **文件系统操作**：读写文件、创建目录、删除、复制、遍历
- **Sandbox 隔离**：支持 macOS Seatbelt、Linux bubblewrap/landlock、Windows restricted token
- **本地模式**：监听本地 WebSocket，配合 SSH 隧道使用
- **远程模式**：注册到自定义 registry，支持远程环境发现

## 快速开始

### 本地模式（推荐）

```sh
# 启动 executor，只监听本地
nova-executor --listen ws://127.0.0.1:8080
```

配合 SSH 隧道：

```sh
ssh -L 8080:127.0.0.1:8080 user@server
# 本地客户端连接 ws://127.0.0.1:8080
```

### 公网模式

```sh
nova-executor \
  --listen wss://0.0.0.0:8080 \
  --auth bearer \
  --auth-token ${NOVA_EXECUTOR_AUTH_TOKEN}
```

### 远程 Registry 模式

```sh
nova-executor \
  --remote https://my-registry.example.com \
  --environment-id my-env-01 \
  --auth bearer \
  --auth-token ${NOVA_EXECUTOR_AUTH_TOKEN}
```

## 命令行参数

```
Options:
      --listen <URL>                    Transport endpoint URL [default: ws://127.0.0.1:8080]
      --concurrent-requests <COUNT>     Maximum concurrent requests per connection [default: 1]
      --remote <URL>                    Remote registry base URL
      --environment-id <ID>             Environment id for remote registration
      --name <NAME>                     Human-readable environment name
      --auth <AUTH>                     Authentication mode [default: none]
      --auth-token <AUTH_TOKEN>         Bearer token [env: NOVA_EXECUTOR_AUTH_TOKEN]
      --executor-self-exe <PATH>        Path to executor executable
      --executor-linux-sandbox-exe <PATH> Path to Linux sandbox helper
      --telemetry                       Enable OpenTelemetry
  -h, --help                            Print help
```

## API

### 进程管理

- `process/start` - 启动进程
- `process/read` - 读取进程输出
- `process/write` - 写入进程 stdin
- `process/terminate` - 终止进程
- `process/signal` - 发送信号

### 文件系统

- `fs/readFile` - 读取文件
- `fs/writeFile` - 写入文件
- `fs/readDirectory` - 列出目录
- `fs/createDirectory` - 创建目录
- `fs/remove` - 删除文件/目录
- `fs/copy` - 复制文件/目录
- `fs/getMetadata` - 获取文件元数据
- `fs/canonicalize` - 规范化路径

### 环境

- `initialize` - 初始化连接
- `initialized` - 确认初始化
- `environment/info` - 获取环境信息
- `environment/status` - 获取环境状态

## 配置

默认配置目录：`~/.nova/executor/`

可用环境变量：

- `NOVA_EXECUTOR_HOME` - 覆盖配置目录
- `NOVA_EXECUTOR_AUTH_TOKEN` - Bearer token

## 与 Nova 集成

`nova-executor` 是**编程无绑定的通用执行后端**：线上协议（见 [PROTOCOL.md](./PROTOCOL.md)）
是唯一产品，任何语言照协议可实现客户端。Nova 侧的接法是把它作为工具引擎后面的
可插拔执行后端（本地 subprocess ↔ executor 同缝切换），工具契约不变。

- Python SDK：`packages/nova-executor-py`（`ExecutorClient`，只做连接）
- 协议版本：`initialize` 响应携带 `protocolVersion`（major 不等即不兼容）

## 开发

```sh
# 编译
cargo build --workspace

# 测试（含 executor-server 集成测试：起真实 server 子进程跑全链路协议）
cargo test --workspace

# 只跑集成测试（tests/ 下每个文件一个套件）
cargo test -p nova-executor-server --test initialize

# 运行 CLI
cargo run -p nova-executor-cli -- --listen ws://127.0.0.1:8080
```

### 集成测试基建（fork 自 codex exec-server）

`crates/executor-server/tests/` 是端到端集成测试：`tests/common/` 夹具把测试二进制
自身经 `#[ctor]` 隐藏入口分派兼任服务器（`exec-server --listen ws://127.0.0.1:0`
子命令）与沙箱 helper（argv1 哨兵），`ExecServerHarness` 负责起子进程、读 stdout
listen URL、WS 连接与 JSON-RPC 收发。与 codex 的差异：不引入 Bazel 专用
test-binary-support/arg0 alias 机械；nova 独有端点（fs/readStream、fs/writeStream）
由源码内联测试覆盖，此处不重复。

## 架构

```
nova_executor/
├── crates/
│   ├── executor-protocol/      # JSON-RPC 协议类型（process/fs/pty/environment/http）
│   ├── executor-server/        # WebSocket server + 进程/文件系统实现 + AuthProvider
│   ├── executor-cli/           # 独立 CLI 入口
│   ├── executor-protocol-core/ # 基础协议类型（exec 层类型子集——agent 语义已移除）
│   ├── executor-file-system/   # 文件系统抽象
│   ├── executor-sandboxing/    # Sandbox 实现（seatbelt/bwrap/landlock/windows）
│   ├── executor-windows-sandbox/ # Windows 沙箱后端（restricted token/elevated，fork 自 codex windows-sandbox-rs）
│   ├── executor-execpolicy/    # 命令策略机械判定层
│   ├── executor-network-proxy/ # 网络代理（managed network sandbox）
│   ├── executor-http-client/   # HTTP 客户端
│   ├── executor-websocket-client/ # WebSocket 客户端
│   ├── executor-otel/          # OpenTelemetry（指标/链路——模型遥测已移除）
│   └── executor-utils-*/       # 工具库
└── Cargo.toml                  # workspace 配置
```

## 通用执行后端化（v1.0 清洗）

本项目从 `codex-rs/exec-server` 迁移而来。v1.0 将其收敛为纯执行后端：

1. **移除模型层**：`executor-codex-api`（responses/realtime/session/compact/memories
   端点、provider、rate limits）整 crate 删除；`AuthProvider` 内移 server。
2. **移除 agent 配置体系**：`executor-config`（permissions/hooks/MCP/skills/marketplace）、
   `executor-model-provider-info`、`executor-extension-items`（Rust 侧工具注册处）删除。
3. **协议清洗**：删除 `capabilityRoots/discoverV1` 端点（agent 插件发现）；
   `initialize` 响应新增 `protocolVersion`（major/minor 协商）。
4. **http/request 门控**：审计日志全量留痕 + `NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS`
   域名白名单（未设置 = 全放行）。
5. **并发默认值**：`--concurrent-requests` 默认 1 → 32（Agent 客户端并行工具调用
   不再被串行化；1 = 串行模式）。

## License

MIT
