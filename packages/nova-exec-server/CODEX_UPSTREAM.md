# codex 上游同步锚点

> nova-exec-server 对齐 codex exec-server 的同步基线与节奏记录。

## 当前锚点

- **commit**：`39f427708738953ac01c40c3d9351236e8ea21c0`
- **上游时间**：2026-10-08 20:49 +0000
- **nova 侧状态**：追新批至此锚点全部清零（批次 1+2 十四项安全修复、A 档七件、
  spawn 管线换代、C+D 完整性全链、d6fb836f31 尾巴，协议 v1.13，CI 八腿全绿）。
  随后两批落地：MXC SDK 1.0.0 迁移（`a0962223`）、RS 迁移完备批（`6263f495`——
  权限词汇全量 + PID 命名空间旋钮 + launcher `--as-pid-1` 条件化存量修正）。
- **原则修正（用户拍板）**：迁移完备优先——引擎先备全量词汇，工具体系升级
  随后消费；禁止以存量工具面窄化引擎。策略"表达能力"（词汇/求值/强制）归
  exec 完备化；策略"分发来源"（codex-config 托管层栈、machine_policy、
  prefer_mxc）另行决策，默认不跟。

## 裁点记录（有意不镜像，审计勿复报）

- **capabilityRoots/discoverV1 + capabilityDiscoveryV2**（对位 codex 39f4277087
  `exec-server-protocol`：`CAPABILITY_ROOTS_DISCOVER_METHOD`、
  `EnvironmentCapabilities.capability_discovery_sandbox` /
  `capability_discovery_v2`、`CapabilityRootDiscoverRequest`、
  `DISCOVERABLE_PLUGIN_MANIFEST_PATHS`）——agent 插件/技能发现不属于通用
  执行后端，整面裁除（v0→v1 清洗已记 discoverV1 于 PROTOCOL.md「已移除」；
  本批补记 v2 能力位同属该面）。
- **NON_INHERITABLE_ENV_VARS 两名**：`CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN`
  （noise 协议）与 `CODEX_GUARDIAN_DECISIONS_API_KEY`（guardian 决策 API）
  属未镜像面，不随全量名单恢复（protocol-core shell_environment.rs 有就地
  裁点注释）。
- **`CODEX_SESSION_ID_ENV_VAR`**：锚点 protocol/shell_environment.rs 的常量，
  其注入方在 codex core `exec_env.rs`（agent 运行时面），nova exec 侧无
  生产/消费方，不镜像。
- **ChatGPT 亲和面**（对位 codex 39f4277087：http-client 的
  `chatgpt_cloudflare_cookies.rs`/`chatgpt_hosts.rs`/`windows_tls.rs` 模块、
  `client_builder.rs`/`outbound_proxy.rs` 的 ChatGPT cookie 仓字段与构造器、
  websocket-client 的握手 cookie 注入/回存（12acac2a66 #46506）与
  `cookie_tests.rs`）——该面为 ChatGPT 后端（chatgpt.com/backend-api）过
  Cloudflare 机器人检测的亲和机器；nova executor 是编程无绑定执行后端，
  模型流量全在 Python 侧 nova_ai（且不走 ChatGPT 后端），整面裁除。
  代码内裁点注释在案（http-client lib.rs:71、websocket-client lib.rs:168/354）。
- **Statsig/会话遥测出口**：`install_global_statsig_settings` 无安装方
  （dead_code 存留备查）、SessionTelemetry/business metrics/会话遥测面
  不镜像（otel crate README 在案）；锚点 `otel/tests/suite/snapshot.rs`
  后两例（`manager_snapshot_metrics_*`/`manager_turn_cost_*`）随该面未移植
  （snapshot.rs 文件头有裁点注释）。
- **远程环境注册面**：Noise 协议、`environment/accepted.rs`（host-accepted
  WebSocket）、`environment/connect_options.rs`（RemoteEnvironmentOptions
  http_headers）、cloud registry 注册/forward 子命令、
  `client/connection_failure.rs`（双变体塌缩为 String，已核实 nova WS 路径
  不丢重试覆盖）、`client_refresh_tests.rs`/`client_transport_tests.rs`——
  codex 云生态的 executor 注册/托管面不镜像；nova 的远程执行归 Python 侧
  SSH/executor 体系（`executor_switch` 扩展）。
- **Windows 沙箱 CI 环境弱化三处**（均为 GHA 托管 runner 与 codex 自管
  runner 池的环境差，带 TODO，真机/自管环境可恢复）：
  `unified_exec/tests.rs` 三个 legacy 后端用例 CI 跳门（:675/:753/:1046——
  hosted runner job-object 层级干扰沙箱后代生命周期）、
  `fs_sandbox_windows_tests.rs` 两例 CI 跳门（:159/:204——hosted-runner
  service session 卡死，commit 2e446cee 实证）、pwsh readiness 超时
  5s→30s（`unified_exec/tests.rs:489`——托管 runner Defender 扫描冷启动）。
  与 ci.yml 既有两个排除项（root_metadata 嵌套盲区、
  legacy_capture_cancellation pwsh7 盲区）同类。
- **Windows 执行别名同名**（挂账待核）：`runtime_ownership.rs` 的
  `APP_CORE_RUNNER_ALIAS = "nova-command-runner.exe"` 与 helper exe 同名
  （锚点刻意不同名：`codex-core-command-runner.exe` ≠ `codex-command-runner.exe`）。
  Rust 侧全树走 const 自洽；执行别名是 MSIX 清单的外部契约，待 nova MSIX
  打包工程核对 `<uap3:ExecutionAlias>` 声明。
- **`EXIT_ON_STDIN_CLOSE` 无 spawn 方注入**：codex 由宿主 spawn 托管
  executor 时注入该 env 实现父死子随；nova 侧目前仅有手工 CLI flag/env
  （`NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE`），spawn 方注入归上层 SDK/包
  体系（出口挂账）。
- **句柄表 per-id 在飞预约层**（`file_handle.rs` 模块头注释在案）：并发同名
  ID 的打开在碰文件之前即拒（先到者胜）——codex 上游仅靠落表复查（后到者
  胜，败者的开门副作用已发生）。nova 有意多加的一层加固，消除败者截断
  副作用；行为差异方向是收紧而非放宽。
- **推送式 fs/readStream 断线/放弃语义**（nova 自有通道，锚点为拉循环）：
  推送路由按 handle_id 键控；客户端放弃即 best-effort fs/close（对位锚点
  拉模式的 drop 收摊）；close→同名重开的代际防护（条目代际校验）为 nova
  自有加固（锚点拉模式无长驻服务端任务，天然无此竞态）；残余窗口
  = 代际判定与阻塞 notify 间隔，彻底闭合需协议级流令牌（明认取舍）。
- **prewarm_shell_snapshot 裁点**：锚点 `process.rs` trait 默认方法 +
  `local_process.rs` 实现 + `CapturePurpose::Prewarm` + 3 个用例——锚点
  唯一生产调用方在 codex core（unified_exec，未镜像面）；nova 无进程内
  Rust 调用方，py 侧若需要应新增线上方法（届时恢复）。
- **Windows managed-network 测试跳门两簇**：`process_sandbox_tests.rs` 的
  `managed_network_honors_windows_sandbox_level` 双用例与 network-proxy
  `tests/windows_stable_ingress.rs` 主路由测试均 `#[ignore]`——windows
  共享 ingress 未移植（`network_proxy_restricting_sid` 恒 None 占位，
  proxy.rs 有裁点注释），恢复 ingress 批次需一并恢复。
- **arg0 启动面三处未镜像**：16MiB 线程栈预算（`THREAD_STACK_SIZE_BYTES`，
  nova `#[tokio::main]` 默认栈）、`load_dotenv`（`~/.codex/.env` 过滤
  `CODEX_` 前缀——codex 产品启动序）、`apply_patch` argv0/argv1 分派臂与
  PATH prepend（codex 的补丁工具，nova 无此物）。
- **linux-sandbox 别名目录跨进程锁收窄**：codex 的 `nova-linux-sandbox`
  软链别名目录带跨进程锁文件；nova 收窄为进程私有 TempDir（无跨进程
  共享面，CLI main.rs 就地注释在案）。
- **MSIX 外部契约族**（与执行别名挂账并案）：`package_identity.rs` 的
  debug 联调白名单保留 `OpenAI.Codex*` 包族（就地注释在案）——发布打包
  时随执行别名一并核对。
- **遥测面小裁点组**：`EnvironmentInfo::local_temporary_directories()`
  公开方法（消费方在 codex core environment_selection，逻辑已内联进
  `local()`）；`EnvironmentNetworkPolicy.requires_proxy` 字段（消费方在
  codex-core orchestrator）；otel `network_policy.rs` OTLP 导出策略门控面；
  otel `exporter`/`trace_exporter` 字段本批未接（`load_otel_settings`
  有 warn—— traces/logs 接线归后续批次）。
- **遥测命名家族约定**：shell snapshot 指标名按 `exec_server_shell_snapshot_*`
  家族（与同文件 `exec_server_*`（计数加 `_total`）惯例一致，R1 更名期
  决策），不按 `codex.*`→`nova.*` 逐字映射为 `nova.shell_snapshot.*`；
  span/事件名统一 `nova.exec_server.*`。
- **行为等价归一挂账（不逐条立项）**：`dirs_next`→`dirs`（env.rs 同一
  Known Folder 语义）；`RetainedOutputChunk(Vec<u8>)` 对锚点
  `ProcessOutputChunk(ByteChunk)`（协议面语义等价，存储/读取各多一次
  深拷贝——性能保真差，R1 期历史决策）；`LocalProcess`/`RemoteProcess`
  可见性 `pub(crate)`→`pub`（tests 消费方需要，锚点 crate 私有）；
  CLI EnvFilter 默认 `warn`（锚点兜底 `error` + otel SDK 静音——otel
  layer 自带 allowlist，SDK 噪声不回流出口）；`--strict-config` 面
  （CLI crate 为 nova 自有，codex-config 严格校验面不随）。
- **seatbelt symlink opt-out 配置源未接入**：`allow_symlinked_nova_home`
  开关于 executor 存在（`with_allowed_symlinked_nova_home` setter），
  但无配置读取方、恒 None——seatbelt.rs:449 的文案与 CODEX_UPSTREAM.md
  本条互为指引；接入配置源（settings/config.toml 归上层）时恢复。

## 同步节奏

- **不逐日追新**：上游每天数十提交，逐日追会把对齐工程变成永远还不完的债。
- **每周一次对账**（或有大版本动作时），一次对账 = fetch + 区间分诊 + 批次移植
  + 对照表刷新 + 锚点前移。
- 每个移植批次的提交信息标注对位 hash；锚点前移时更新本文件。

## 对账方法（沉淀自批次 1+2 的教训，逐条执行防漏）

1. `git -C <codex> fetch origin main` 后，区间清单：
   `git log --no-merges <旧锚点>..origin/main -- <镜像路径集>`（镜像路径集 = 各
   crate 对应目录 + `protocol/src/permissions*`/`sandbox.rs` 等子集镜像点）。
2. **逐提交读 diff**（`git show <hash>`），不允许只读提交说明/统计——分诊结论
   必须来自补丁本体（历史教训：只看说明曾把顶层落点误判为 legacy 平铺）。
3. 每项标注：直移 / 适配（注裁点） / 不适用（未镜像面） / 决策项（给用户拍板）。
4. **crate 级文件清单 diff** 兜底（`git ls-tree -r` vs `find`），防"crate 在但
   模块漏镜像"的粒度漏检。
5. **配对文件内容 diff 抽查**（沉淀自 2026-10-13 五路审计的教训）：文件清单
   对齐不等于内容对齐——审计曾抓出"文件在、内容停在上游旧形态"一族
   （bwrap bind-data fd 共享回退 #50059 修复、VmSocketRestricted 缺失、
   fs_sandbox 别名规整丢 macOS 分支等）。每次对账对镜像面内**按行数加权
   抽查配对文件的内容 diff**（归一化命名映射后），残留 hunk 必须逐条有
   "对位"注释，否则立项修复。
6. 移植纪律：以 codex 代码为唯一准绳、逐 hunk 对位、不发明、不做范围外修复；
   nova 自有取舍（命名映射/裁点）在注释标"对位 codex <hash>"并注明理由。
7. 验证链：本机 cargo test（合并态）→ zig windows-gnu 交叉编译 → 245 服务器
   Linux 实测（bwrap 族）→ py 双包测试 → CI 八腿逐腿核实。
   **245 实测注意**：rsync 后必须 `find crates -type f \( -name '*.rs' -o
   -name '*.toml' \) -exec touch {} +`——rsync 保留旧 mtime 会让 cargo 指纹
   命中陈旧二进制（实战踩过）。
