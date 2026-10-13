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
