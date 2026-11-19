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
- **远程环境注册面主链已恢复**：Noise 两端、Registry 注册/连接/验证、Rendezvous
  接入、forwarder、Direct 端点与远程 runner 已移植；自有 `relay-protocol` crate
  承载公开 DTO/Protobuf，`relay-server` crate 提供 Nova 自有 SQLite Registry 与
  密文 WS Rendezvous。鉴权替换为 Nova enrollment bearer token，Noise prologue
  与 Protobuf package 使用 Nova 品牌域，Python 经 `nova-exec-relay-client --stdio`
  复用 ExecutorClient。真实 Rust 端到端已覆盖 enroll→远程节点→Noise harness→进程输出。
  host-accepted WebSocket 已恢复初始接管、替换 socket 与原会话恢复，7 项真实套件全绿；
  `aws-auth` crate 与 CLI SigV4 provider/旗标已恢复，签名和网络策略测试通过。
  Nova 自有公共服务只提供 Noise Registry/Rendezvous；Direct 使用 codex 对位的外部
  Registry/acceptor，不再为自有服务发明另一套 Direct 数据面。旧“整面不镜像”裁决作废。
- **Windows 沙箱 CI 环境弱化三处**（均为 GHA 托管 runner 与 codex 自管
  runner 池的环境差，带 TODO，真机/自管环境可恢复）：
  `unified_exec/tests.rs` 三个 legacy 后端用例 CI 跳门（:675/:753/:1046——
  hosted runner job-object 层级干扰沙箱后代生命周期）、
  `fs_sandbox_windows_tests.rs` 两例 CI 跳门（:159/:204——hosted-runner
  service session 卡死，commit 2e446cee 实证）、pwsh readiness 超时
  5s→30s（`unified_exec/tests.rs:489`——托管 runner Defender 扫描冷启动）。
  与 ci.yml 既有两个排除项（root_metadata 嵌套盲区、
  legacy_capture_cancellation pwsh7 盲区）同类。
- **Windows 执行别名同名已闭环**：`runtime_ownership.rs` 的
  `APP_CORE_RUNNER_ALIAS = "nova-core-command-runner.exe"`，与 helper exe 区分
  （锚点刻意不同名：`codex-core-command-runner.exe` ≠ `codex-command-runner.exe`）。
  MSIX 清单 `<uap3:ExecutionAlias>` 仍待打包工程核对（真机验证待工具链）。
- **`EXIT_ON_STDIN_CLOSE` 注入已闭环**：本地 WS spawn 显式注入
  `NOVA_EXEC_SERVER_EXIT_ON_STDIN_CLOSE=1`，并把子进程 stdin 保持为父进程私管，
  父进程死亡/管道关闭时 executor 收到 EOF 退出（对位 ParentLifetime::StdinPipe）。
  spawn 回归断言同时覆盖环境变量与 `stdin=PIPE`。
- **句柄表 per-id 在飞预约层**（`file_handle.rs` 模块头注释在案）：并发同名
  ID 的打开在碰文件之前即拒（先到者胜）——codex 上游仅靠落表复查（后到者
  胜，败者的开门副作用已发生）。nova 有意多加的一层加固，消除败者截断
  副作用；行为差异方向是收紧而非放宽。
- **推送式 fs/readStream 断线/放弃语义**（nova 自有通道，锚点为拉循环）：
  推送路由按 handle_id 键控；客户端放弃即 best-effort fs/close（对位锚点
  拉模式的 drop 收摊）；close→同名重开的代际防护已升级为**每条目操作锁**——
  读通知/写落盘与 close/reopen 串行化，原“代际判定与阻塞 notify 间隔”残余
  窗口已闭合（不再需要协议级流令牌）。写侧单块在飞窗口同样由该锁闭合。
- **prewarm_shell_snapshot 裁点**：锚点 `process.rs` trait 默认方法 +
  `local_process.rs` 实现 + `CapturePurpose::Prewarm` + 3 个用例——锚点
  唯一生产调用方在 codex core（unified_exec，未镜像面）；nova 无进程内
  Rust 调用方，py 侧若需要应新增线上方法（届时恢复）。
- **Windows managed-network 共享 ingress 已闭环**：`windows_proxy_ingress` /
  `windows_tcp_attribution` 已移植并接入共享路由与 restricting SID 门控；
  `network_proxy_restricting_sid` 由活动路由颁发。相关 `#[ignore]` 主路由测试
  仍待 Windows 真机/工具链恢复（本机缺 MinGW 头文件）。
- **arg0 启动面三处未镜像**：16MiB 线程栈预算（`THREAD_STACK_SIZE_BYTES`，
  nova `#[tokio::main]` 默认栈）、`load_dotenv`（`~/.codex/.env` 过滤
  `CODEX_` 前缀——codex 产品启动序）、`apply_patch` argv0/argv1 分派臂与
  PATH prepend（codex 的补丁工具，nova 无此物）。
- **linux-sandbox 别名目录跨进程锁收窄**：codex 的 `nova-linux-sandbox`
  软链别名目录带跨进程锁文件；nova 收窄为进程私有 TempDir（无跨进程
  共享面，CLI main.rs 就地注释在案）。
- **MSIX 外部契约族已闭环**：`package_identity.rs` 的 debug 联调白名单已改为
  `Nova.ExecServer*` 包族，不再保留 `OpenAI.Codex*`；执行别名已改为
  `nova-core-command-runner.exe`（与 helper exe 区分）。
  发布打包时仍需核对 MSIX 清单与包家族一致性（真机验证待工具链）。
- **遥测面小裁点组**：`EnvironmentInfo::local_temporary_directories()`
  公开方法（消费方在 codex core environment_selection，逻辑已内联进
  `local()`）；`EnvironmentNetworkPolicy.requires_proxy` 字段（消费方在
  codex-core orchestrator）；otel `network_policy.rs` OTLP 导出策略门控已移植
  （`PolicyExporter` + 可吊销 `RuntimeHttpClient`）；`exporter`/`trace_exporter`
  配置字段已接通，OTLP HTTP 已改回共享 route-aware client（原直接 reqwest 已替换）。
- **遥测命名家族约定**：shell snapshot 指标名按 `exec_server_shell_snapshot_*`
  家族（与同文件 `exec_server_*`（计数加 `_total`）惯例一致，R1 更名期
  决策），不按 `codex.*`→`nova.*` 逐字映射为 `nova.shell_snapshot.*`；
  span/事件名统一 `nova.exec_server.*`。
- **行为等价归一挂账（不逐条立项）**：`dirs_next`→`dirs`（env.rs 同一
  Known Folder 语义）；`RetainedOutputChunk` 已改为直接持有协议 `ByteChunk`
  （Arc<Vec<u8>>），保留与读取均为 Arc 克隆，消除此前两次深拷贝；
  `LocalProcess`/`RemoteProcess` 可见性 `pub(crate)`→`pub`（tests 消费方需要，
  锚点 crate 私有）；CLI EnvFilter 默认 `warn`（锚点兜底 `error` + otel SDK 静音——otel
  layer 自带 allowlist，SDK 噪声不回流出口）；`--strict-config` 面
  （CLI crate 为 nova 自有，codex-config 严格校验面不随）。
- **seatbelt symlink opt-out 配置源已接入**：`~/.nova/exec-server/config.toml`
  的 `allow_symlinked_nova_home = true` 会经 CLI 注入 runtime paths；环境别名
  仅在规范化目标等于实际 executor home 时保留（对位 codex 同名语义）。
  原“setter 恒 None”状态已解除。
- **`sandbox_home` 线上/落盘词汇更名**（对位 codex `codex_home`）：
  provisioning 管道消息（`deny_unknown_fields`）与 InstallationRecord
  注册表 JSON 的字段名同步更名——serde 字段级契约变更，全链自洽
  （双端同仓共享单一定义，`PROVISIONING_PROTOCOL_VERSION=1` 预发布期，
  错配对端硬报错而非静默）；alpha 无存量部署，旧注册表记录仅影响
  alpha→alpha 升级。
- **uds crate 不镜像**：daemon 目录函数并入 `seatbelt_daemon.rs`
  （逐行对位锚点 `uds/src/daemon_directory.rs`，模块头注释在案）。
- **pty 三处 nova 保留件**（R1 期）：unix 上 `arg0.is_some()` 改走
  `spawn_process_preserving_fds` 第三分派臂（裸名 argv0 被 PATH 重解析的
  劫持面，收紧方向）；windows_tests 的 `wait_for_output_contains("> ")`
  替代固定 `sleep(200ms)`（Ctrl-C 后吞命令防护，增强方向）；home-dir
  默认 `~/.nova/exec-server`（超出命名映射的 `/exec-server` 子层，全树
  README/PROTOCOL/CLI/测试自洽）。
- **linux-sandbox 别名目录位置与守卫已闭环**：别名落点恢复为 `<executor home>/tmp/arg0`
  （0700 权限 + release 下拒绝系统临时目录守卫 + 每进程目录锁 + 陈旧目录清理），
  不再使用会被按龄清理的系统临时目录；janitor 用例覆盖“只删未持锁目录”。
  Linux 真机/交叉编译验证仍待工具链。
- **CLI 测试面两件不随迁**：`local_exec_server_ignores_invalid_config_
  without_strict_config`（我方 eprintln 会回显 toml 错误原文片段——与
  锚点"stderr 不含原文"的反向断言冲突，响亮策略系刻意，main.rs:205
  注释在案）；`exec_server_telemetry_tests.rs`（信号监听失败注入例——
  我方 `run_until_shutdown` 为 Immediate 单形态、未拆
  `run_until_shutdown_with_signals` 接缝，无落点；正常路径已被
  SIGTERM/stdin 两 e2e 覆盖）。
- **trace 传播测试竞态已闭环**：`rpc.rs` / `client.rs` 的传播用例不再 ignore，
  通过测试内全局遥测锁把 subscriber/callsite 兴趣缓存的切换串行化，后台任务
  不再被并发测试缓存为禁用；相关用例在 cargo test 下已恢复并通过。
- **shell-command 族裁**：`command_safety`/`parse_command`/`powershell`
  （含 `powershell_tree_sitter_tests.rs`）——审批/命令安全分析概念不属于
  通用执行后端（src/lib.rs:3-4 就地注释）。
- **bundled_bwrap bazel 件不随迁**：锚点 `tests/suite/bundled_bwrap.rs`
  （BAZEL_PACKAGE 门控的篡改摘要测试）——nova 无 Bazel 面，cargo 通道
  该守卫本就空转；bundled bwrap 摘要校验拒绝路径（exit code 8 已镜像）
  两侧 cargo 通道均无覆盖。
- **network-proxy 小件组**：`managed_proxy_routing()` getter 与
  `is_non_public_ip` 再导出未随（消费方在未镜像的 codex core/rmcp-client）；
  `mitm_ca`/`NetworkMitmCaConfig` 线上字段未保留（MITM 整面裁点的一部分，
  `from_effective_config` 的无条件拒绝臂与对应用例连带缺席）；
  `write_attribution_frame` 已单点归 network-proxy，linux-sandbox 仅 import 复用，
  不再双实现（读写两端共用同一魔数与长度校验）。
- **http-client 计时器放宽族**（`outbound_proxy_redirect_coverage_tests.rs`
  2s→10s ×6、`route_aware_client_pool_tests.rs` 2s→10s ×2、
  `route_aware_tls_fallback_tests.rs` 5s→15s/120s 等）：3cc7170e/74970e96
  负载竞态批的有意放宽（文件注释在案；120s 处为"等到条件即走、上限只
  防悬挂"的非墙钟同步）。

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
