# nova-exec-server 线上协议（v1.12）

> 本文件是 nova-exec-server 服务端与客户端之间的**唯一契约**。任何语言照本文档
> 可实现客户端。协议语义只覆盖**执行**（进程/文件系统/PTY/环境/HTTP 代发），
> 不含 agent、模型、会话等上层概念。

- 传输：JSON-RPC 2.0 over WebSocket 回环（`ws://`）或 stdio 管道
  （`--listen stdio`，CLI/桌面/SSH 隧道场景；`wss://` 不支持——服务端
  只做回环承载，TLS 归上层隧道/中继层）
- 版本协商：`initialize` 响应携带 `protocolVersion`（`"major.minor"`）；
  **major 不等即不兼容**（客户端应拒绝连接），minor 只增不减（新能力新字段）
- 服务端版本常量：`crates/exec-server-protocol/src/lib.rs::PROTOCOL_VERSION`
- 鉴权：executor 只做本地回环（WS 回环 / stdio 承载），**无入站鉴权**；
  对外暴露与鉴权归上层中继层，不归 executor

## 生命周期

```
client → initialize {clientName, resumeSessionId?}
client ← {sessionId, protocolVersion, environmentInfo?}
client → initialized（notification）
……任意请求/通知……
```

- `resumeSessionId`：恢复既有会话（**进程**随会话存活——文件句柄表是连接级
  状态，断连即清空，重连（含 resume 同会话）后 fs 句柄全部失效（-32004），
  续传客户端须重新开门）。
- `environmentInfo`（v1.2 起，可选）：initialize 捎带的执行端环境元数据，
  **形状与 `environment/info` 响应完全一致**——客户端应直接缓存，省一次
  `environment/info` 往返；旧服务端缺省该字段时，客户端在首次需要时回退
  单次 `environment/info` 调用并缓存（serde 向后兼容，两形态互通）。
- 连接断开：该连接启动的进程被清理（会话级生命周期）。
- 客户端入站限流（v1.7 起，对位 codex 54487a5b61）：**客户端侧**对执行端
  发来的请求/未知通知/畸形消息限 8 KiB（超限即断开连接并带原因），数据面
  通知（`process/output|exited|closed`、`http/request/bodyDelta`）放宽至
  2 MiB，nova 扩展数据面 `fs/readStream/chunk` 按协议最大块放宽至
  8 MiB（nova 特有豁免点），响应/错误不受此限（仍受传输层单消息上限约束）；
  服务端→客户端反向请求（`network/policyRequest` 等）的 `tracestate` 超
  512B 即丢弃（保留 `traceparent`）；stdio stderr 日志按 8 KiB 分块读取。
  服务端侧入站不新增限制（沿用既有传输上限）。
- 服务端行为约定（实现实况）：
  - `initialize` 每连接仅一次（重复调用报错）；
  - 未知方法报 method_not_found；**未知通知直接关闭连接**（严格姿态）；
  - `http/request` 的 `requestId` 仅在**流式**（`streamResponse=true`）活跃期内
    必须唯一（服务端按此查重）；非流式请求不查重；
  - 控制面预留容量：environment/info|status、process/signal|terminate、
    fs/close 在大流量数据面（读流/写流）饱和时仍可通行；
  - `fs/writeStream/chunk` 在 initialize 完成前到达只告警忽略；
  - `/readyz`：WS 监听上另挂一个无鉴权 HTTP readiness 端点。

## 方法一览

### 环境

| 方法 | 参数 | 结果 | 说明 |
|---|---|---|---|
| `environment/info` | — | `EnvironmentInfo` | shell/cwd/`userHomeDir`（`~` 展开目标，v1.2）/`platformOs`（`std::env::consts::OS` 值，v1.2）/临时目录（`temporaryDirectories` + `tempDir`，v1.2）/`executorVersion`（v1.7，执行端发布版本，旧执行端缺省 `"0.0.0"`）/`providerId`（v1.7，可选，不透明构建身份；nova 无 build-stamp 基建，恒省略）/`prependPathDirs`（v1.7，执行端 PATH 前置目录，空即省略）/能力位（`networkProxyLaunch`（v1.3 起恒 true——托管网络代理已落地）、`environmentConfigRead`（v1.4 起恒 true——端点已恢复为 nova 语义）、`sandboxedFileStreaming`（v1.6 起恒 true——fs 流式通道可按请求装配沙箱执行，约束位补回）、`fileWriteStreaming`（v1.9 起恒 true——fs/open 的 replace 模式与 fs/writeBlock 已落地；v1.7-1.8 恒 false）、`httpHeaderEnvVars`（v1.5 起恒 true——valueEnvVar 机制已实现的补宣告）、`shellSnapshotV2`（unix 为 true，非 unix 恒 false）、`windowsMxc`（v1.8——windows 端按 MXC 沙箱可用性如实上报，非 windows 恒 false；客户端按位门控后再下发 `windowsSandboxLevel="mxc"`）、`linuxRootWritePreservesDevices`（v1.10——Linux 沙箱在 `/` 可写时保留标准设备；Linux 恒 true，其余平台恒 false）、`linuxApprovedRootWritePreservesRestrictions`（v1.10——approved 根写保留设备与被拒根元数据符号链接目标；Linux 恒 true，其余平台恒 false；两位均 opt-in，false 时线上省略））。v1.2 起 initialize 响应捎带同形状数据，客户端通常无需再调本方法（仅旧服务端回退用） |
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
  1. user 层：`~/.nova/exec-server/config.toml`（TOML，executor 自有环境配置；
     `NOVA_EXEC_SERVER_HOME` 可覆盖其所在目录），`format: "toml"`，
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

`~/.nova/exec-server/config.toml` 初版 schema（平坦三件套，全部可选；
**现状：仅 `environmentConfig/read` 投影回读，无任何解析/执法实现**——
"缺省 full"、"能力开关覆盖"等语义为将来预留，当前下发不生效）：

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
| `process/start` | 启动进程。参数：`processId`（客户端选定的连接内句柄）、`metadata?`（v1.7，`{threadId?, toolCallId?}`——遥测归因预留，服务端存而不取，不参与鉴权/调度）、`argv`、`cwd`（PathUri）、`env`、`envPolicy?`、`shellSnapshot?`（ShellSnapshotRequest，`{scopeId, shell}`）、`tty`、`pipeStdin?`、`arg0?`、`sandbox?`（FileSystemSandboxContext）、`enforceManagedNetwork?`、`managedNetwork?`（v1.7 补 `allowUnixSockets`/`dangerouslyAllowAllUnixSockets`——缺省受限：独立 unix socket 默认拒绝，仅 allow-all 时 Linux 沙箱放行 AF_UNIX）、`networkProxy?`（RemoteNetworkProxyLaunchConfig，v1.3 起真实生效，见「托管网络」） |
| `process/read` | 读输出（`waitMs` 轮询等待；响应含 `sandboxDenied: bool`——执行端把失败归类为沙箱拒绝的标记） |
| `process/write` | 写 stdin |
| `process/signal` | 发信号 |
| `process/terminate` | 终止 |
| → `process/output` | 通知：输出增量（stdout/stderr/pty） |
| → `process/exited` | 通知：退出（exitCode；`sandboxDenied: bool?`——三态：true/false/未报告（null）） |
| → `process/closed` | 通知：句柄关闭 |

**沙箱**：每次 `process/start` 由客户端下发沙箱意图（`sandbox` 字段 +
managed network 参数），服务端解析为具体 wrapper（macOS Seatbelt /
Linux bubblewrap+landlock / Windows restricted token）。**策略在客户端，
执行在 executor。** `sandbox.windowsSandboxLevel` 字段自 v1.7 起承载实现选择
（`disabled`/`restricted-token`/`elevated`/`mxc`——对位 codex
WindowsSandboxSelection；**MXC 实现已随 Windows 批次落地**（a896e32f，
v1.8 起能力位 `windowsMxc` 按可用性如实上报——客户端按位门控后下发，
不可用时 `invalid_params`）；`ExecResponse.sandboxType` 同步补 `windowsMxc`
枚举值）。v1.7 起删除 `windowsSandboxPrivateDesktop` 字段（对位 codex
a633ebc124：传统 Windows 沙箱恒使用私有桌面）。

**托管网络**（v1.3 起，能力位 `networkProxyLaunch` 门控）：`process/start`
携带 `networkProxy`（RemoteNetworkProxyLaunchConfig）时，executor 在进程
启动前拉起一个进程级本地网络代理（HTTP CONNECT + SOCKS5，监听 loopback
临时端口），并向子进程注入代理环境变量（`HTTP_PROXY`/`HTTPS_PROXY`/
`ALL_PROXY` 等 + `NOVA_EXEC_SERVER_NETWORK_PROXY_ACTIVE=1`）；同次启动带 `sandbox`
时，沙箱网络段断直连（默认拒绝出网，仅放行代理 loopback 端口），全部出网
流量被迫经代理接受域名白名单裁决。代理生命周期随进程：进程句柄
`process/closed` 时代理一并关闭（继承输出流的后台子进程存续期间代理保持
可用）。fail-closed 语义：`networkProxy.proxy.enabled=false` 或代理无法
启动时 `process/start` 直接报错，不静默裸跑；`enforceManagedNetwork: true`
而沙箱内无可用代理端点时，沙箱网络段 fail closed（空网络规则）。
- `networkProxy.policyDecisionTimeoutMs`（可选，非零）：开启服务端→客户端
  反向裁决回调。基线策略未覆盖的目标经 `network/policyRequest`（请求，
  `{processId, request: {protocol, host, port}}`）询问控制端，控制端回
  `{decision: {...}}`——`{type: "allow"}`（无 reason 字段）/
  `{type: "deny", reason}` / `{type: "ask", reason}`（reason 必填）；
  超时/连接断开一律按 deny 处理。启用回调时 `processId` 必须非空且 ≤256 字节。
- → `network/policyDecision`（通知，best-effort 可丢）：代理每次最终裁决
  （allow/deny/ask）后向控制端发审计事件
  `{processId, timestamp, scope, decision, source, reason, protocol, host,
  port, method?, client?, policyOverride}`；仅审计用途，丢失不影响裁决。

**Shell Snapshot**（unix only，能力位 `shellSnapshotV2` 门控）：客户端在
`process/start` 携带 `shellSnapshot: {scopeId, shell}` 时，executor 把登录
shell 启动状态（`.zshrc`/`.bashrc` 求值结果：函数/别名/setopt/导出环境）
缓存在进程内，缓存键为 `request(scopeId + shell) + cwd + envPolicy +
sandbox`（同 scopeId 不同 shell 不命中——key 含 shell）。`scopeId` 非空且
≤256 字节（违即 `invalid_params`）；`shell.name` 只认 `bash`/`zsh`/`sh`
（其余 `invalid_params`）；非 unix 端下发即 `invalid_params`。
仅对 `<shell.path> -lc <command>` 形态的启动生效（其余 argv 原样放行）；
命中缓存的后续启动跳过启动文件，改用 bash `-pc` / zsh `-fc`（sh 为 `-c`）
加 eval 恢复脚本执行原命令——快照 state 切成 ≤60KB 的环境变量
（`__NOVA_EXEC_SERVER_SHELL_SNAPSHOT_STATE_<n>`）传入子进程。首次执行时同步跑一次
捕获（10s 超时、失败按 1s 退避最多 3 次、并发单飞），缓存上限 LRU 16 条、
单条 512KB；捕获失败永远回退为原始 `shell -lc` 行为。快照环境在捕获后仍
按当次 `envPolicy` 过滤，且剔除托管代理注入的代理变量与 `PWD`/`OLDPWD`。
非 unix 端收到 `shellSnapshot` 字段返回 `invalid_params`。

### 文件系统

| 方法 | 说明 |
|---|---|
| `fs/readFile` | 小文件读取（base64；服务端上限 512MB，超限报错） |
| `fs/open` / `fs/readBlock` / `fs/writeBlock` / `fs/close` | 随机访问句柄（每连接上限 128 个，在飞行打开也占槽；重复 ID/超限在触碰文件前拒绝；**句柄 ID ≤32 字节**，超限 `invalid_request`——read/write 两侧校验器同规）。`fs/open` 带 `mode?`（`read`（缺省）|`replace`——v1.9 起 `replace` 生效：创建/截断写打开，带沙箱上下文时按**写权限档**进沙箱 helper 开门执法，全盘整读不豁免）；`fs/writeBlock`（v1.9）显式 `offset` 定位写——`chunk` 为非空 base64 块（解码后 ≤1MiB），写区间不得超 i64 上限（越限 `invalid_request`），成功响应即确认全部字节落盘；写侧由能力位 `fileWriteStreaming` 门控 |
| `fs/readStream` (+ `fs/readStream/chunk` / `fs/readStream/done` 通知） | 大文件流式读取。沙箱语义：带平台沙箱上下文时经一次性沙箱化 `fs_helper` 开门并把 fd/handle 传回 executor 自读（受限范围生效）；不带时 executor 直读 |
| `fs/writeStream`（请求开句柄）+ `fs/writeStream/chunk`（通知，客户端→服务端，seq 严格序 append）+ `fs/writeStream/done`（请求收尾确认） | 大文件流式写入。v1.12 起收编句柄族底层：与 `fs/open` 同一开门链路（带平台沙箱上下文时经一次性沙箱化 `fs_helper` 开门并把 fd/handle 传回 executor，受限范围生效；不带时 executor 直开），chunk 经与 `fs/writeBlock` 共享的定位写核心落到每流追加游标，句柄与 readBlock/writeBlock 同一张连接级句柄表（容量/ID 校验共享）。**中断语义翻转（v1.12）：中止/断连不再删半成品**，文件留在盘上（对齐 writeBlock/scp 等一切上传工具，为断点续传让路）。**断点续传**：开句柄带 `offset?`——None = 创建/截断（现状语义）；Some(n) = 不截断、从 n 续写（n 不得超当前文件长度，文件不存在时 n>0 拒绝，均 `invalid_request`）；`done` 的 `totalBytes` 为全量语义（offset 起点 + 本次流式字节数，客户端对账用）。chunk 块上限 4MB；done 要求流已见 eof 块；乱序/超限/写盘失败由 done 回报首个错误。**队列有界背压（v1.12 后补）**：chunk 为 fire-and-forget 通知——服务端不再拖慢发送方，队列满（客户端持续领先落盘 16 块）流终态失败，done 回报 `-32600` "queue full"（客户端语义=降速后重试）；服务端内存恒有界（最坏 16×4MB/流）。**done 一次性**：done 后句柄摘除，重复 done（串行）或连接关闭后 done 均 unknown handle（-32004；与首个 done 并发竞达的重复 done 可能落 -32603 task 退出竞态）。**done 错误码表**：乱序/块超 4MB/eof 后再来块/未见 eof 即 done/对非流句柄 done/queue full → 均 -32600；offset 越界/文件不存在且 n>0 → -32600；句柄不存在 → -32004；写盘失败按底层 `io::ErrorKind` 透传（PermissionDenied → -32600，ENOSPC 等 → -32603）；通知参数畸形 → 连接级协议错误关连接 |
| `fs/writeFile` | 写文件（base64） |
| `fs/readDirectory` | 列目录（服务端上限 50 000 条，超限 -32603） |
| `fs/createDirectory` | 建目录（`recursive?`） |
| `fs/remove` | 删除 |
| `fs/copy` | 复制 |
| `fs/getMetadata` | 元数据 |
| `fs/canonicalize` | 路径规范化 |
| `fs/walk` | 目录遍历（`WalkOptions`） |

所有路径用 **PathUri**（`file:///` URI），由服务端按本机路径规则解释；
`sandbox` 字段（FileSystemSandboxContext）限定可访问根。

**FileSystemSandboxContext**（v1.11 终态形状）：沙箱策略与解释策略所需的执行端
路径。线上字段：

- `permissions`：权限档案（显式路径以 executor file URI 序列化）；
- `policyContext`：`{cwd, workspaceRoots}`——**策略目录的唯一承载**（v1.11 起；
  v1.10 沿 codex 保留的平铺 `cwd`/`workspaceRoots` 双形状字段已删除）。`cwd`
  为策略锚定目录（绝对权限也需要；进程可用不同的工作目录）。客户端整个省略
  `policyContext` 时 executor 入口回退为自身 cwd（人机工学：`process/start`
  回退进程 `cwd`，fs 方法回退 executor 自身当前目录）；但策略含 cwd 依赖项
  （相对 glob / `project_roots` 符号）而省略时 `invalid_params`；
- `userHomeDir?`/`temporaryDirectories?`：执行端家目录/临时目录（解析 `~`
  相对与 `:tmpdir` 条目）；
- `windowsSandboxLevel`/`windowsSandboxProxySettingsMode?`/`useLegacyLandlock`。

**读写路由**（v1.10，对位 codex a4ee536f01）：全部 fs 操作按读/写**各自**权限档
分流——读类操作（`fs/readFile`/`fs/readStream`/`fs/getMetadata`/`fs/readDirectory`/
`fs/canonicalize`/`fs/walk`）仅在读受限时进沙箱，写类操作（`fs/writeFile`/
`fs/writeStream`/`fs/createDirectory`/`fs/remove`/`fs/copy`）仅在写受限时进沙箱，
`fs/open` 按 `mode` 分流（read 看读档、replace 看写档）；**全盘整读不再被写株连**
（写受限、读全盘的上下文里读操作直读不进沙箱）；临时目录规则按执行端路径约定
判定（`:slash_tmp` 拒绝仅在 POSIX 约定算读限制）。外来平台的显式权限路径
（URI 约定与本机不兼容）一律 fail-closed 按需沙箱处理，不得选中非沙箱直读/直写。

**followSymlinks**（`fs/readFile` / `fs/writeFile` / `fs/createDirectory` /
`fs/getMetadata` / `fs/remove` 五端点，可选 bool，缺省 = true 即旧行为）：
`false` 时启用 no-follow 语义——服务端经 rustix openat 族（Windows 为
NtCreateFile + OBJ_DONT_REPARSE）逐组件打开路径，任一组件是符号链接即报
`invalid_request`；no-follow 的 `fs/remove` 不支持 `recursive: true`
（报 Unsupported）。

### HTTP 代发

| 方法 | 说明 |
|---|---|
| `http/request` | executor 代发 HTTP(S)：`method`、`url`、`requestId` 必填；`headers?`、`bodyBase64?`、`timeoutMs?`、`redirectPolicy?`（默认 follow）、`streamResponse?`（默认 false）可选 |
| → `http/request/bodyDelta` | 通知：流式响应体（`requestId`、`seq`、`deltaBase64`、`done`、`error?`） |

`headers` 条目形态：`{name, value, valueEnvVar?}`。`valueEnvVar` 由执行端
环境变量填值（`value` 作前缀，如 `value: "Bearer "`）——凭据不跨线委派：
客户端点名变量名，值由发起 HTTP 的进程在服务端读取。**保护名单拦截**
（对位 codex HTTP_HEADER_ENV_DENYLIST）：`NOVA_EXEC_SERVER_PROXY_ATTRIBUTION_TOKEN`、
供应商 key（`VOLCENGINE_API_KEY`/`MOONSHOT_API_KEY`/`KIMI_API_KEY`）与通用云
凭据（`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_SESSION_TOKEN`/
`AZURE_CLIENT_SECRET`/`AZURE_FEDERATED_TOKEN_FILE`/`GOOGLE_APPLICATION_CREDENTIALS`
——具名清单，`AWS_PROFILE` 等不在面内）点名即
`-32602 invalid_params` 拒绝（大小写不敏感）；变量缺失或空值同样 -32602。

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

## 环境变量清单（用户/部署可见）

- `NOVA_EXEC_SERVER_HOME` — executor 家目录（`~/.nova/exec-server`）覆盖
- `NOVA_EXEC_SERVER_CA_CERTIFICATE` / `SSL_CERT_FILE` — 自定义 CA 证书路径
  （企业自签场景；影响 `http/request` 与 WS TLS 信任链）
- `NOVA_EXEC_SERVER_EXEC_SERVER_EXIT_ON_STDIN_CLOSE` — **WS 托管 spawn** 时父进程
  持有 stdin 管道、关闭即退出（父死子随）；**stdio 形态下为 no-op**（stdin 本是
  传输线，EOF 自然结束服务）
- `NOVA_EXEC_SERVER_NETWORK_PROXY_ACTIVE` — 打进沙箱进程环境：标记"活在托管
  网络代理里"（executor 自设，勿手设）
- `NOVA_EXEC_SERVER_PROXY_ATTRIBUTION_TOKEN` — 代理归因 token（审计归属；executor 自设）
- `NOVA_EXEC_SERVER_NETWORK_ALLOW_LOCAL_BINDING` — 网络沙箱内允许绑本地端口
  （内部 wire 标记）
- `NOVA_EXEC_SERVER_WINDOWS_SANDBOX_PROXY_PORTS` — Windows 沙箱回环代理端口清单
  （内部 wire 标记）
- 构建期：`NOVA_EXEC_SERVER_BWRAP_SHA256`（bundled bwrap 的 pin 校验——当前无
  生产者，休眠链路）

## 版本变迁

完整版本注释以 `crates/exec-server-protocol/src/lib.rs::PROTOCOL_VERSION`
上方注释为准；本文件只记当前版本面的关键增量。

- **v1.9**（可写文件流落地，对位 codex 10/2 启用三提交）：
  - `fs/open` 支持 `mode="replace"`（创建/截断写打开，写权限档分流执法——
    全盘整读不豁免写）；`fs/writeBlock` 显式 offset 定位写（非空块 ≤1MiB、
    i64 偏移上限）；
  - 句柄表 128 槽含在飞行打开（重复 ID/容量超限在碰文件前拒绝）；
  - 能力位 `fileWriteStreaming` 翻 true；
  - 地基：读侧 `has_full_disk_read_access_for_convention` 与
    `should_read_from_sandbox`/`should_write_into_sandbox` 分流对（全量读写
    路由归 v1.10）。
- **v1.12**（writeStream 收编句柄族 + 上传断点续传——nova 自有通道）：
  - `fs/writeStream` 与 `fs/open` 共享开门链路与连接级句柄表，chunk 经与
    `fs/writeBlock` 共享的定位写核心落每流追加游标；长命沙箱 helper 写管道
    拆除（沙箱路径改走开门 fd 传递，与 readStream 同一姿态）；
  - `FsWriteStreamParams` 新增可选 `offset`：Some(n) = 不截断、从 n 续写
    （越界/文件不存在且 n>0 拒绝）；`done.totalBytes` 为全量语义（见
    「文件系统」节 writeStream 行）；
  - 中断语义翻转：中止/断连不再删半成品，文件留在盘上；
  - fs helper IPC 升 v2（nova 自有内部协议）：`FsHelperRequest` 删除
    `WriteStream` 变体，`FsHelperOpenParams.mode` 换 `OpenMode` 并补
    `resume`（不截断写打开）。
  - 后补三项（版本号未动，语义透明增量）：①队列有界背压——chunk 通知
    fire-and-forget，队列满（领先落盘 16 块）流终态失败、done 报 -32600
    "queue full"（ResourceBusy 归 -32600，与兄弟违规同档）；②done 一次性
    ——done 后句柄摘除，重复/断连后 done 均 -32004；③并发同名句柄拒绝
    竞态安全化（per-id 在飞预约，重复 ID 在碰文件前即拒——此前并发
    分发下落表复查才拒、败者截断副作用已发生）。另：py 客户端权限内层
    键名归正 snake_case（曾误配 camelCase alias）。
- **v1.11**（policyContext 终态化）：删除 v1.10 沿 codex 841b5490b2 保留的
  legacy 平铺 `cwd`/`workspaceRoots` 双形状字段（nova 从未对外发布，没有老
  客户端存在，不背 legacy 包袱）——策略目录只经 `policyContext` 承载；保留
  "客户端整个省略时 executor 入口回退自身 cwd"的人机工学（**形状不兼容
  演化**，客户端须同步升级）。
- **v1.10**（fs 策略语义修正组，对位 codex 2926014075/34e74fda0e/c53f342fec/
  841b5490b2/a4ee536f01/645b683a9e）：
  - `FileSystemSandboxContext` 策略 `cwd` 改必填，新增 `policyContext`
    `{cwd, workspaceRoots}` 子对象（v1.11 已终态化为唯一承载）；
  - 全部 fs 操作按读/写各自权限档分流，全盘整读不再被写株连（见「文件系统」节
    「读写路由」）；
  - 策略匹配 URI-native：策略条目与特殊根按执行机路径约定解析为 PathUri，
    包含/重叠/优先级用校验过的 URI 组件判定，组件边界歧义 fail-closed；
  - deny-read 按执行机路径语义（Windows glob 大小写不敏感 + 分隔符规整，
    POSIX 保字节导向；畸形路径/约定不兼容/非法 glob 全部 fail-closed）；
  - `EnvironmentCapabilities` 补 `linuxRootWritePreservesDevices`/
    `linuxApprovedRootWritePreservesRestrictions` 两位（opt-in，false 省略；
    Linux 恒 true）。

## 客户端

- Python SDK：`packages/nova-exec-server-client`（`ExecutorClient`——initialize 时
  做 protocolVersion major 匹配，不等即 `ProtocolError`）。写流客户端门
  （v1.9，对位 codex e7798c9944）：`fs.open(mode=replace)` 与 `fs.write_block`
  先查缓存的 `environmentInfo.capabilities.fileWriteStreaming`，false 即本地
  `ProtocolError`（「exec-server does not support writable file streams」），
  不发线上请求——旧执行端会把 replace 静默降级为只读句柄
