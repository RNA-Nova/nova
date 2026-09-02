# nova-executor 线上协议（v1.0）

> 本文件是 nova-executor 服务端与客户端之间的**唯一契约**。任何语言照本文档
> 可实现客户端。协议语义只覆盖**执行**（进程/文件系统/PTY/环境/HTTP 代发），
> 不含 agent、模型、会话等上层概念。

- 传输：JSON-RPC 2.0 over WebSocket（`ws://` / `wss://`）
- 版本协商：`initialize` 响应携带 `protocolVersion`（`"major.minor"`）；
  **major 不等即不兼容**（客户端应拒绝连接），minor 只增不减（新能力新字段）
- 服务端版本常量：`crates/executor-protocol/src/lib.rs::PROTOCOL_VERSION`
- 鉴权：WS 升级请求头 `Authorization: Bearer <token>`（`--auth bearer` 时校验；
  本地模式 `--auth none`）

## 生命周期

```
client → initialize {clientName, resumeSessionId?}
client ← {sessionId, protocolVersion}
client → initialized（notification）
……任意请求/通知……
```

- `resumeSessionId`：恢复既有会话（进程/文件句柄随会话存活）。
- 连接断开：该连接启动的进程被清理（会话级生命周期）。

## 方法一览

### 环境

| 方法 | 参数 | 结果 | 说明 |
|---|---|---|---|
| `environment/info` | — | `EnvironmentInfo` | shell/cwd/临时目录/能力位（`networkProxyLaunch`、`environmentConfigRead`） |
| `environment/status` | — | `EnvironmentStatus` | 环境状态 |

### 进程

| 方法 | 说明 |
|---|---|
| `process/start` | 启动进程。参数：`processId`（客户端选定的连接内句柄）、`argv`、`cwd`（PathUri）、`env`、`envPolicy?`、`tty`、`pipeStdin?`、`arg0?`、`sandbox?`（FileSystemSandboxContext）、`enforceManagedNetwork?`、`managedNetwork?`、`networkProxy?` |
| `process/read` | 读输出（`waitMs` 轮询等待） |
| `process/write` | 写 stdin |
| `process/signal` | 发信号 |
| `process/terminate` | 终止 |
| → `process/output` | 通知：输出增量（stdout/stderr/pty） |
| → `process/exited` | 通知：退出（exitCode） |
| → `process/closed` | 通知：句柄关闭 |

**沙箱**：每次 `process/start` 由客户端下发沙箱意图（`sandbox` 字段 +
managed network 参数），服务端解析为具体 wrapper（macOS Seatbelt /
Linux bubblewrap+landlock / Windows restricted token）。**策略在客户端，
执行在 executor。**

### 文件系统

| 方法 | 说明 |
|---|---|
| `fs/readFile` | 小文件读取（base64） |
| `fs/open` / `fs/readBlock` / `fs/close` | 随机访问句柄 |
| `fs/readStream` (+ `fs/readStream/chunk` / `fs/readStream/done` 通知） | 大文件流式读取 |
| `fs/writeFile` | 写文件（base64） |
| `fs/readDirectory` | 列目录 |
| `fs/createDirectory` | 建目录（`recursive?`） |
| `fs/remove` | 删除 |
| `fs/copy` | 复制 |
| `fs/getMetadata` | 元数据 |
| `fs/canonicalize` | 路径规范化 |
| `fs/walk` | 目录遍历（`WalkOptions`） |

所有路径用 **PathUri**（`file:///` URI），由服务端按本机路径规则解释；
`sandbox` 字段（FileSystemSandboxContext）限定可访问根。

### HTTP 代发

| 方法 | 说明 |
|---|---|
| `http/request` | executor 代发 HTTP(S)：`method`、`url`、`headers`、`bodyBase64?`、`timeoutMs?`、`redirectPolicy`、`streamResponse?`、`requestId` |
| → `http/request/bodyDelta` | 通知：流式响应体（`seq`、`deltaBase64`、`done`、`error?`） |

**门控**：每次调用记审计日志（method+url）；环境变量
`NOVA_EXECUTOR_HTTP_ALLOW_DOMAINS`（逗号分隔域名后缀）设置后仅白名单
域名及其子域可访问，未设置 = 全放行。

### PTY

PTY 复用进程族方法：`process/start` 传 `tty: true`，输出经
`process/output`（stream=`pty`）推送，`process/write` 写入。

## 已移除（v0 → v1 清洗）

- `capabilityRoots/discoverV1`（agent 插件/技能发现——不属于执行后端）
- `EnvironmentCapabilities.capabilityDiscoverySandbox` 字段
- 模型 API / 会话 / compact / memories 等 Codex agent 端点（随 `executor-codex-api` 整 crate 移除）

## 客户端

- Python SDK：`packages/nova-executor-py`（`ExecutorClient`——initialize 时
  做 protocolVersion major 匹配，不等即 `ProtocolError`）
