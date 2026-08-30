# nova-executor 线上协议（v1.4）

> 本文件是 nova-executor 服务端与客户端之间的**唯一契约**。任何语言照本文档
> 可实现客户端。协议语义只覆盖**执行**（进程/文件系统/PTY/环境/HTTP 代发），
> 不含 agent、模型、会话等上层概念。

- 传输：JSON-RPC 2.0 over WebSocket（`ws://` / `wss://`）
- 版本协商：`initialize` 响应携带 `protocolVersion`（`"major.minor"`）；
  **major 不等即不兼容**（客户端应拒绝连接），minor 只增不减（新能力新字段）
- 服务端版本常量：`crates/executor-protocol/src/lib.rs::PROTOCOL_VERSION`
- 鉴权：executor 只做本地回环（WS 回环 / stdio 承载），**无入站鉴权**；
  对外暴露与鉴权归上层中继层，不归 executor

## 生命周期

```
client → initialize {clientName, resumeSessionId?}
client ← {sessionId, protocolVersion, environmentInfo?}
client → initialized（notification）
……任意请求/通知……
```

- `resumeSessionId`：恢复既有会话（进程/文件句柄随会话存活）。
- `environmentInfo`（v1.2 起，可选）：initialize 捎带的执行端环境元数据，
  **形状与 `environment/info` 响应完全一致**——客户端应直接缓存，省一次
  `environment/info` 往返；旧服务端缺省该字段时，客户端在首次需要时回退
  单次 `environment/info` 调用并缓存（serde 向后兼容，两形态互通）。
- 连接断开：该连接启动的进程被清理（会话级生命周期）。

## 方法一览

### 环境

| 方法 | 参数 | 结果 | 说明 |
|---|---|---|---|
| `environment/info` | — | `EnvironmentInfo` | shell/cwd/`userHomeDir`（`~` 展开目标，v1.2）/`platformOs`（`std::env::consts::OS` 值，v1.2）/临时目录（`temporaryDirectories` + `tempDir`，v1.2）/能力位（`networkProxyLaunch`（v1.3 起恒 true——托管网络代理已落地）、`environmentConfigRead`（v1.4 起恒 true——端点已恢复为 nova 语义）、`readStream`、`writeStream`、`shellSnapshotV2`（unix 为 true，非 unix 恒 false））。v1.2 起 initialize 响应捎带同形状数据，客户端通常无需再调本方法（仅旧服务端回退用） |
| `environment/status` | — | `EnvironmentStatus` | 环境状态 |
| `environmentConfig/read` | `EnvironmentConfigReadParams` | `EnvironmentConfigReadResponse` | 代读 executor 本机配置层栈（v1.4 起，能力位 `environmentConfigRead` 门控，见下节） |

**环境配置代读**（v1.4 起）：executor 是"代读的手"——客户端够不到远程机器
的盘，executor 读自己所在机器的配置层、按键路径投影后**如实回传层栈**；
**不合并不裁决**（层合并与 trust 裁决归客户端——nova 的 trust 体系在
客户端，executor 不做门控）。

- 请求：`cwd`（PathUri，定位 project 层）+ `configPaths`（键路径选择器，
  如 `[["sandbox"], ["network", "mode"]]`；至少一条路径、每条至少一个键段，
  否则 `invalid_params`——不允许整文档读取）。
- 层栈（`config.layers`，**从低到高优先级排序，两层恒在**）：
  1. user 层：`~/.nova/executor/config.toml`（TOML，executor 自有环境配置；
     `NOVA_EXECUTOR_HOME` 可覆盖其所在目录），`format: "toml"`，
     `baseDir` = executor home；
  2. project 层：`<cwd>/.nova/settings.json`（JSON，与 nova 体系项目级配置
     一致），`format: "json"`，`baseDir` = `<cwd>/.nova`。
- 每层字段：`source`（不透明诊断串，形如 `user:<绝对路径>` /
  `project:<绝对路径>`）、`baseDir`（层内相对路径的基准目录）、`format`、
  `content`（投影后原文，路径值未经归一化）、`error?`。
- **投影语义**：键路径汇成前缀树；命中子树原样保留，未命中键剔除；选择器
  深入标量之下时非表/非对象祖先原样保留（使其仍可覆盖更低层）。投影为空的
  层不从栈中剔除（nova 与 codex 的分歧点：codex 剔空层，nova 固定两层保序）。
- **容错**：文件缺失 = 空层（TOML 空文档 `""` / JSON 空文档 `"{}"`）不回错；
  解析或读取失败 = 该层 `error` 字段带回（content 为空），整个调用不失败。
- 响应另带 `userHomeDir`（`~` 展开目标）、`executorHomeDir`、`hostname`
  （诊断用）；`cloudInsertionIndex` 为预留对位字段，nova 无云配置层，恒等于
  `layers.len()`。

`~/.nova/executor/config.toml` 初版 schema（平坦三件套，全部可选）：

```toml
[sandbox]
level = "workspace-write"   # 本环境沙箱上界：read-only | workspace-write | full（缺省=不约束）
[network]
mode = "full"               # off | full（缺省 full）；allow_domains 预留
[capabilities]              # 能力开关覆盖（缺省全自动探测）
```

### 进程

| 方法 | 说明 |
|---|---|
| `process/start` | 启动进程。参数：`processId`（客户端选定的连接内句柄）、`argv`、`cwd`（PathUri）、`env`、`envPolicy?`、`shellSnapshot?`（ShellSnapshotRequest，`{scopeId, shell}`）、`tty`、`pipeStdin?`、`arg0?`、`sandbox?`（FileSystemSandboxContext）、`enforceManagedNetwork?`、`managedNetwork?`、`networkProxy?`（RemoteNetworkProxyLaunchConfig，v1.3 起真实生效，见「托管网络」） |
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

**托管网络**（v1.3 起，能力位 `networkProxyLaunch` 门控）：`process/start`
携带 `networkProxy`（RemoteNetworkProxyLaunchConfig）时，executor 在进程
启动前拉起一个进程级本地网络代理（HTTP CONNECT + SOCKS5，监听 loopback
临时端口），并向子进程注入代理环境变量（`HTTP_PROXY`/`HTTPS_PROXY`/
`ALL_PROXY` 等 + `NOVA_EXECUTOR_NETWORK_PROXY_ACTIVE=1`）；同次启动带 `sandbox`
时，沙箱网络段断直连（默认拒绝出网，仅放行代理 loopback 端口），全部出网
流量被迫经代理接受域名白名单裁决。代理生命周期随进程：进程句柄
`process/closed` 时代理一并关闭（继承输出流的后台子进程存续期间代理保持
可用）。fail-closed 语义：`networkProxy.proxy.enabled=false` 或代理无法
启动时 `process/start` 直接报错，不静默裸跑；`enforceManagedNetwork: true`
而沙箱内无可用代理端点时，沙箱网络段 fail closed（空网络规则）。
- `networkProxy.policyDecisionTimeoutMs`（可选，非零）：开启服务端→客户端
  反向裁决回调。基线策略未覆盖的目标经 `network/policyRequest`（请求，
  `{processId, request: {protocol, host, port}}`）询问控制端，控制端回
  `{decision: {type: allow|deny|ask, reason?}}`；超时/连接断开一律按
  deny 处理。启用回调时 `processId` 必须非空且 ≤256 字节。
- → `network/policyDecision`（通知，best-effort 可丢）：代理每次最终裁决
  （allow/deny/ask）后向控制端发审计事件
  `{processId, timestamp, scope, decision, source, reason, protocol, host,
  port, method?, client?, policyOverride}`；仅审计用途，丢失不影响裁决。

**Shell Snapshot**（unix only，能力位 `shellSnapshotV2` 门控）：客户端在
`process/start` 携带 `shellSnapshot: {scopeId, shell}` 时，executor 把登录
shell 启动状态（`.zshrc`/`.bashrc` 求值结果：函数/别名/setopt/导出环境）
缓存在进程内，缓存键为 `scopeId + cwd + envPolicy + sandbox` 四元组。
仅对 `<shell.path> -lc <command>` 形态的启动生效（其余 argv 原样放行）；
命中缓存的后续启动跳过启动文件，改用 bash `-pc` / zsh `-fc`（sh 为 `-c`）
加 eval 恢复脚本执行原命令——快照 state 切成 ≤60KB 的环境变量
（`__NOVA_EXECUTOR_SHELL_SNAPSHOT_STATE_<n>`）传入子进程。首次执行时同步跑一次
捕获（10s 超时、失败按 1s 退避最多 3 次、并发单飞），缓存上限 LRU 16 条、
单条 512KB；捕获失败永远回退为原始 `shell -lc` 行为。快照环境在捕获后仍
按当次 `envPolicy` 过滤，且剔除托管代理注入的代理变量与 `PWD`/`OLDPWD`。
非 unix 端收到 `shellSnapshot` 字段返回 `invalid_params`。

### 文件系统

| 方法 | 说明 |
|---|---|
| `fs/readFile` | 小文件读取（base64） |
| `fs/open` / `fs/readBlock` / `fs/close` | 随机访问句柄 |
| `fs/readStream` (+ `fs/readStream/chunk` / `fs/readStream/done` 通知） | 大文件流式读取。沙箱语义：带平台沙箱上下文时经一次性沙箱化 `fs_helper` 开门并把 fd/handle 传回 executor 自读（受限范围生效）；不带时 executor 直读 |
| `fs/writeStream`（请求开句柄）+ `fs/writeStream/chunk`（通知，客户端→服务端，seq 严格序 append）+ `fs/writeStream/done`（请求收尾确认） | 大文件流式写入。**中断不产生可见文件**（中止/断连/乱序删半截）。沙箱语义：带平台沙箱上下文时经长命沙箱化 `fs_helper` 子进程持续写（受限范围生效；executor 逐帧转发 chunk/done，半截文件经沙箱内删除）；不带时 executor 自写 |
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

**followSymlinks**（`fs/readFile` / `fs/writeFile` / `fs/createDirectory` /
`fs/getMetadata` / `fs/remove` 五端点，可选 bool，缺省 = true 即旧行为）：
`false` 时启用 no-follow 语义——服务端经 rustix openat 族（Windows 为
NtCreateFile + OBJ_DONT_REPARSE）逐组件打开路径，任一组件是符号链接即报
`invalid_request`；no-follow 的 `fs/remove` 不支持 `recursive: true`
（报 Unsupported）。

### HTTP 代发

| 方法 | 说明 |
|---|---|
| `http/request` | executor 代发 HTTP(S)：`method`、`url`、`headers`、`bodyBase64?`、`timeoutMs?`、`redirectPolicy`、`streamResponse?`、`requestId` |
| → `http/request/bodyDelta` | 通知：流式响应体（`seq`、`deltaBase64`、`done`、`error?`） |

注：executor 本体代发无内置白名单/审计（对齐 codex）；出网管控归
`process/start` 的托管网络段（见「托管网络」，v1.3 起）与将来的中继层，
不归这个端点。

### PTY

PTY 复用进程族方法：`process/start` 传 `tty: true`，输出经
`process/output`（stream=`pty`）推送，`process/write` 写入。

## 已移除（v0 → v1 清洗）

- `capabilityRoots/discoverV1`（agent 插件/技能发现——不属于执行后端）
- `EnvironmentCapabilities.capabilityDiscoverySandbox` 字段
- 模型 API / 会话 / compact / memories 等 Codex agent 端点（随 `executor-codex-api` 整 crate 移除）

## 客户端

- Python SDK：`packages/nova-executor-client`（`ExecutorClient`——initialize 时
  做 protocolVersion major 匹配，不等即 `ProtocolError`）
