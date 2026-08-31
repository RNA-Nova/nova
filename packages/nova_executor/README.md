# nova-executor

`nova-executor` 是 Nova 的通用执行后端，从 OpenAI Codex 的 `codex-exec-server`
抽离并独立化。**编程无绑定**：不知道 agent/模型/工具/会话概念；线上协议
（[PROTOCOL.md](./PROTOCOL.md)）即产品，任何语言照协议可实现客户端。

## 能力

- **进程管理**：`process/start|read|write|terminate|signal`（含 PTY：start 传 `tty: true`）
- **文件系统**：读/写/目录/删除/复制/遍历/元数据 + 大文件流式 `fs/readStream`（服务端推送分片）/ `fs/writeStream`（客户端分片推）
- **沙箱隔离**：macOS Seatbelt / Linux bubblewrap+landlock+seccomp / Windows restricted token——策略由客户端经 `process/start` 的 `sandbox` 参数逐请求下发
- **托管网络沙箱**：进程网络经本地代理执法（域名名单 allow/deny + `network/policyRequest` 反向裁决 + `network/policyDecision` 审计通知）
- **HTTP 代发**：`http/request`（缓冲/流式），`valueEnvVar` 凭据不跨线委派（执行端环境变量填值，敏感变量有保护名单拦截）
- **环境元数据**：`environment/info|status`（initialize 捎带）+ `environmentConfig/read`（代读执行机配置层）

## 快速开始

```sh
# stdio 形态（本地/SSH 承载，CLI/桌面推荐——父进程直接持有管道）
nova-executor --listen stdio

# WebSocket 回环形态（配合 SSH 隧道或本机客户端）
nova-executor --listen ws://127.0.0.1:8080
```

SSH 远程场景不需要公网监听：远程 executor 只回环监听，经 SSH 隧道承载
（nova 侧的供给与隧道编排归客户端包）。

## 命令行参数

```
--listen <URL>                     传输端点：ws://IP:PORT（默认 ws://127.0.0.1:8080）或 stdio
--concurrent-requests <COUNT>      单连接并发请求数 [默认 32；1 = 串行模式]
--exit-on-stdin-close              父死子随：WS 托管 spawn 时父进程 stdin 管道
                                   关闭即退出（stdio 形态下 EOF 本就结束服务）
--executor-self-exe <PATH>         executor 可执行文件路径（隐藏 helper 模式用）
--executor-linux-sandbox-exe <PATH> Linux 沙箱 helper 路径
```

隐藏 helper 模式（argv 哨兵分派，不属公开 CLI）：沙箱化 fs 操作 helper
与 arg0 exec helper。

## 鉴权

executor 本体**无入站鉴权**——只做本地回环承载（stdio 管道 / WS 回环）。
对外暴露与鉴权归上层（SSH 隧道 / 将来的中继层），不归 executor。

## 配置与环境变量

配置目录默认 `~/.nova/executor/`（`NOVA_EXECUTOR_HOME` 覆盖）。

用户可见环境变量：

- `NOVA_EXECUTOR_HOME` — 配置目录覆盖
- `NOVA_EXECUTOR_CA_CERTIFICATE` / `SSL_CERT_FILE` — 自定义 CA（企业自签场景，
  影响 `http/request` 与 WS TLS 信任）
- `NOVA_EXECUTOR_EXEC_SERVER_EXIT_ON_STDIN_CLOSE` — stdio 托管 spawn 时
  父进程管道关闭即退出（父死子随）

## 与 Nova 集成

Nova 侧的接法是把 executor 作为工具引擎后面的可插拔执行后端（本地
subprocess ↔ executor 同缝切换），工具契约不变。

- Python SDK：`packages/nova-executor-client`（`ExecutorClient`，只做连接；
  传输双形态 + 恢复 + 控制/数据面分离 + 网络裁决回调）
- 协议版本：`initialize` 响应携带 `protocolVersion`（major 不等即不兼容，当前 v1.4）

## 开发

```sh
cargo build --workspace          # 编译
cargo test --workspace           # 测试（含 executor-server 端到端集成套件）
cargo test -p nova-executor-server --test initialize   # 单套件
cargo run -p nova-executor-cli -- --listen ws://127.0.0.1:8080   # 运行 CLI
```

### 集成测试基建（fork 自 codex exec-server）

`crates/executor-server/tests/` 是端到端集成测试：`tests/common/` 夹具把测试
二进制自身经 `#[ctor]` 隐藏入口分派兼任服务器与沙箱 helper（argv 哨兵），
`ExecServerHarness` 负责起子进程、读 listen URL、WS 连接与 JSON-RPC 收发。
与 codex 的差异：不引入 Bazel 专用 test-binary-support/arg0 alias 机械。

## 架构

```
crates/
├── executor-protocol/          # 线上协议类型（initialize/process/fs/http/environment/网络策略）
├── executor-protocol-core/     # 基础协议类型（权限/路径/环境子集——agent 语义已移除）
├── executor-server/            # JSON-RPC server（ws/stdio）+ 进程/文件系统/网络沙箱接线
├── executor-cli/               # 独立 CLI 入口（隐藏 helper 模式分派）
├── executor-file-system/       # 文件系统抽象与沙箱上下文
├── executor-sandboxing/        # 沙箱策略编译（seatbelt 策略生成/landlock/共享机械）
├── executor-linux-sandbox/     # Linux 沙箱 helper（bwrap+landlock+seccomp）
├── executor-windows-sandbox/   # Windows 沙箱后端（restricted token/elevated/WFP）
├── executor-network-proxy/     # 托管网络代理（HTTP CONNECT/SOCKS5 + 域名策略 + 审计）
├── executor-http-client/       # HTTP 客户端（http/request 代发底座）
├── executor-websocket-client/  # WebSocket 客户端
├── executor-otel/              # OpenTelemetry 基建（指标/链路导出）
├── executor-shell-command/     # shell 命令处理（快照/检测）
├── executor-utils-*/           # 工具库（absolute-path/cargo-bin/home-dir/path-uri/pty/string/rustls-provider/async-utils）
└── Cargo.toml                  # workspace 配置
```

## 通用执行后端化（v1.0 清洗）

本项目从 `codex-rs/exec-server` 迁移而来。v1.0 将其收敛为纯执行后端：

1. **移除模型层**：`executor-codex-api`（responses/realtime/session/compact/memories
   端点、provider、rate limits）整 crate 删除；鉴权归上层中继（未落地前 executor
   只做回环承载）。
2. **移除 agent 配置体系**：`executor-config`（permissions/hooks/MCP/skills/marketplace）、
   `executor-model-provider-info`、`executor-extension-items`（Rust 侧工具注册处）删除。
3. **协议清洗**：删除 `capabilityRoots/discoverV1` 端点（agent 插件发现）；
   `initialize` 响应新增 `protocolVersion`（major/minor 协商）。
4. **http/request 凭据保护**：`valueEnvVar` 头代发有敏感环境变量保护名单
   （执行端 token/供应商 key/云凭据点名即 -32602 拒绝）。
5. **并发默认值**：`--concurrent-requests` 默认 1 → 32（Agent 客户端并行工具调用
   不再被串行化；1 = 串行模式）。

## License

Apache-2.0（继承自 codex 的 Apache-2.0 许可，NOTICE/归属声明见各 crate）
