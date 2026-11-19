# nova-exec-server

`nova-exec-server` 是 Nova 的通用执行后端，从 OpenAI Codex 的 `codex-exec-server`
抽离并独立化。**编程无绑定**：不知道 agent/模型/工具/会话概念；线上协议
（[PROTOCOL.md](./PROTOCOL.md)）即产品，任何语言照协议可实现客户端。

## 能力

- **进程管理**：`process/start|read|write|terminate|signal`（含 PTY：start 传 `tty: true`）
- **文件系统**：读/写/目录/删除/复制/遍历/元数据 + 大文件流式 `fs/readStream`（服务端推送分片）/ `fs/writeStream`（客户端分片推）
- **沙箱隔离**：macOS Seatbelt / Linux bubblewrap+landlock+seccomp / Windows restricted token——策略由客户端经 `process/start` 的 `sandbox` 参数逐请求下发
- **托管网络沙箱**：进程网络经本地代理执法（域名名单 allow/deny + `network/policyRequest` 反向裁决 + `network/policyDecision` 审计通知）
- **HTTP 代发**：`http/request`（缓冲/流式），`valueEnvVar` 凭据不跨线委派（执行端环境变量填值，敏感变量有保护名单拦截）
- **环境元数据**：`environment/info|status`（initialize 捎带）+ `environmentConfig/read`（代读执行机配置层）
- **Noise Relay 远程接入**：Nova Registry/Rendezvous + 混合 Noise 加密 + forwarder + Python stdio 桥，契约见 [RELAY_PROTOCOL.md](./RELAY_PROTOCOL.md)

## 快速开始

```sh
# stdio 形态（本地/SSH 承载，CLI/桌面推荐——父进程直接持有管道）
nova-exec-server --listen stdio

# WebSocket 回环形态（配合 SSH 隧道或本机客户端）
nova-exec-server --listen ws://127.0.0.1:0
# Noise Relay 远程节点（公共服务部署见 RELAY_PROTOCOL.md）
nova-exec-server --remote https://relay.example.com \
  --environment-id machine-a --registry-token '<node token>'
```

SSH 可作为通用承载方式：远程 executor 只回环监听，由调用方自行建立隧道；
executor 本体不感知 SSH，Nova 也不再提供自动供给与隧道编排。

## 命令行参数

```
--listen <URL>                     传输端点：ws://IP:PORT（默认 ws://127.0.0.1:0 临时端口）或 stdio
--concurrent-requests <COUNT>      单连接并发请求数 [默认 1 = 串行模式（与 codex 对齐）；≥2 并发]
--exit-on-stdin-close              父死子随：WS 托管 spawn 时父进程 stdin 管道
                                   关闭即退出（stdio 形态下 EOF 本就结束服务）
--executor-self-exe <PATH>         executor 可执行文件路径（隐藏 helper 模式用）
--executor-linux-sandbox-exe <PATH> Linux 沙箱 helper 路径
--remote <URL>                     注册到 Nova Registry 并主动连接 Rendezvous
--environment-id <ID>              已 enrollment 的远程环境 ID
--remote-transport <noise|direct>  远程承载（默认 noise）
--registry-token <TOKEN>           节点 token（推荐环境变量注入）
--remote-transport direct --aws-sigv4 [--aws-profile P] [--aws-region R] [--aws-service S]
                                    Direct 的 AWS SigV4 注册与 WS 握手签名
nova-exec-server forward --connect <WS_URL> --remote <URL> ...  Noise forwarder
nova-exec-relay-client             harness 的 Noise Relay → stdio 桥
nova-dev-ssh-node                 开发期 OpenSSH 反向隧道节点（默认仅 127.0.0.1:22）
nova-dev-ssh-client               开发期 OpenSSH 本地监听 / ProxyCommand stdio 客户端
nova-exec-relay-server             Nova Registry + Rendezvous 服务
```

隐藏 helper 模式（argv 哨兵分派，不属公开 CLI）：沙箱化 fs 操作 helper
与 arg0 exec helper。

## 鉴权

executor 本体默认只做本地回环承载（stdio 管道 / WS 回环），WS 入站鉴权可选开启。
对外暴露与鉴权归上层（SSH 隧道 / Nova Relay）；Relay Registry 使用 Nova 自有
enrollment token，详见 RELAY_PROTOCOL.md。

## 配置与环境变量

配置目录默认 `~/.nova/exec-server/`（`NOVA_EXEC_SERVER_HOME` 覆盖）。

用户可见环境变量：

- `NOVA_EXEC_SERVER_HOME` — 配置目录覆盖
- `NOVA_EXEC_SERVER_CA_CERTIFICATE` / `SSL_CERT_FILE` — 自定义 CA（企业自签场景，
  影响 `http/request` 与 WS TLS 信任）
- `NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE` — stdio 托管 spawn 时
  父进程管道关闭即退出（父死子随）

### 遥测（可选，默认全静默）

`config.toml` 的 `[otel]` 段配置 OTLP metrics 出口（对位 codex `[otel]` 语义——
没配 = 零外发 noop；配了只流向自配收集端，无官方通道）：

```toml
[otel]
environment = "prod"           # 可选，默认 production
metrics_exporter = { OtlpGrpc = { endpoint = "http://127.0.0.1:4317" } }
# 或 HTTP：metrics_exporter = { OtlpHttp = { endpoint = "...", protocol = "Json" } }
```

daemon 启动时读取并装配 provider（`nova-exec-server-otel` 的 `load_otel_settings` +
`OtelProvider::try_new`，对位 codex `build_provider`），退出时 flush。metrics、
traces/logs 均经共享 route-aware HTTP client 与网络策略出口。

## 与 Nova 集成

Nova 侧的接法是把 executor 作为工具引擎后面的可插拔执行后端（本地
subprocess ↔ executor 同缝切换），工具契约不变。

- Python SDK：`packages/nova_exec_server_client`（`ExecutorClient`，只做连接；
  传输双形态 + 恢复 + 控制/数据面分离 + 网络裁决回调）
- 协议版本：`initialize` 响应携带 `protocolVersion`（major 不等即不兼容，当前 v1.13）

## 开发

```sh
cargo build --workspace          # 编译
cargo test --workspace           # 测试（含 exec-server 端到端集成套件）
cargo test -p nova-exec-server --test initialize   # 单套件
cargo run -p nova-exec-server-cli -- --listen ws://127.0.0.1:0     # 运行 CLI
```

### 集成测试基建（fork 自 codex exec-server）

`crates/exec-server/tests/` 是端到端集成测试：`tests/common/` 夹具把测试
二进制自身经 `#[ctor]` 隐藏入口分派兼任服务器与沙箱 helper（argv 哨兵），
`ExecServerHarness` 负责起子进程、读 listen URL、WS 连接与 JSON-RPC 收发。
与 codex 的差异：不引入 Bazel 专用 test-binary-support/arg0 alias 机械。

## 架构

```
crates/
├── exec-server-protocol/          # 线上协议类型（initialize/process/fs/http/environment/网络策略）
├── exec-server-protocol-core/     # 基础协议类型（权限/路径/环境子集——agent 语义已移除）
├── exec-server-relay-protocol/    # Registry DTO、Noise 公钥与 Protobuf Relay 帧正典
├── exec-server-relay-server/      # Nova 自有 Registry + Rendezvous（SQLite/密文 WS 路由）
├── exec-server-aws-auth/          # Direct 的 AWS 凭证链、SigV4 签名与网络策略接线
├── exec-server/                   # JSON-RPC server + Noise Relay 两端 + 执行/沙箱接线
├── exec-server-cli/               # 独立 CLI 入口（隐藏 helper 模式分派）
├── exec-server-file-system/       # 文件系统抽象与沙箱上下文
├── exec-server-sandboxing/        # 沙箱策略编译（seatbelt 策略生成/landlock/共享机械）
├── exec-server-linux-sandbox/     # Linux 沙箱 helper（bwrap+landlock+seccomp）
├── exec-server-windows-sandbox/   # Windows 沙箱后端（restricted token/elevated/WFP）
├── exec-server-network-proxy/     # 托管网络代理（HTTP CONNECT/SOCKS5 + 域名策略 + 审计）
├── exec-server-http-client/       # HTTP 客户端（http/request 代发底座）
├── exec-server-websocket-client/  # WebSocket 客户端
├── exec-server-otel/              # OpenTelemetry 基建（指标/链路导出）
├── exec-server-shell-command/     # shell 命令处理（快照/检测）
├── exec-server-utils-*/           # 工具库 7 件（absolute-path/cargo-bin/home-dir/
│                                  #   path-uri/pty/rustls-provider/string）
└── Cargo.toml                     # workspace 配置
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
5. **并发默认值**：`--concurrent-requests` 默认保持 1（Inline 串行，与 codex 对齐，
   结构上消灭跨请求竞态）；显式调到 ≥2 才并发，仅建议"慢 handler + 并行调用"场景。

## License

Apache-2.0（继承自 codex 的 Apache-2.0 许可，NOTICE/归属声明见各 crate）
