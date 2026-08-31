# nova_executor 对齐 codex exec-server 的完整设计

> 目标：executor 作为"协议即产品"的通用执行后端，补齐与 codex exec-server 的差距；
> 环境层（如引入）对接 nova 体系而非 codex config 体系。
> 依据：代码级对比报告（79 文件 1:1 对应、~90% 重命名级等价的 fork 关系实证）。

## 定位（不变）

- executor 不知道 agent/模型/工具/会话概念——纯执行后端
- 策略在客户端，执行在 executor
- 协议即产品（PROTOCOL.md 为线上契约）

## 补齐清单（分层）

### 层 0：硬伤修复（立即，一两小时）

| # | 项 | 位置 | 修法 |
|---|---|---|---|
| 1 | 虚假能力位 `environment_config_read: true` 但未注册 | `executor-protocol/src/protocol.rs:165-168` | 改 `false` + 清 `executor-protocol/src/environment_config.rs` 类型残留 + PROTOCOL.md:32 同步 |
| 2 | Windows 沙箱编译断裂 | `executor-sandboxing/Cargo.toml:19`（依赖被注释）vs `spawn.rs:54`/`manager.rs:518`（仍引用） | 恢复依赖，或给引用处补 `#[cfg]` 门控——Windows 目标编译过 |
| 3 | 文档洞：流式读不支持平台沙箱 | `PROTOCOL.md:59` 补声明（行为在 `local_file_system.rs:101-105`） | 补一句边界声明 |
| 4 | 11 个 `#[ignore]` 测试 | `local_process.rs` 10 处 + `client.rs:1701` | network-proxy 实现已在——恢复测试 |

### 层 1：执行层对齐（近期）

| # | 项 | 位置 | 说明 |
|---|---|---|---|
| 5 | **PDEATHSIG** | `executor-sandboxing/src/spawn.rs` 的 spawn 点（或 executor-server `client_transport.rs` 的 stdio_command_process） | Linux pre_exec + `prctl(PR_SET_PDEATHSIG, SIGTERM)` + getppid 防竞态（codex spawn.rs:99-102 同款）。覆盖 SIGKILL/崩溃的父死清场。macOS 无此机制（登记簿继续兜底） |
| 6 | 恢复沙箱测试 | seatbelt_tests.rs（1455 行）+ violation_tests.rs（237 行） | fork 时丢失——从 codex 蓝图恢复（crate 重命名适配） |
| 7 | fs/readStream 的 Rust 客户端封装 | `executor-server/src/client.rs` + `remote_file_system.rs` | 我们加了服务端推送协议（fs/readStream+chunk/done）但只有 Python SDK 消费——Rust 客户端还走 readBlock 循环。补齐 Rust 侧封装，内部对称 |

### 层 2：环境层（战略决策项——对接 nova 体系）

**前提决策**：是否引入"环境"一等概念（LLM 环境感知 + 多环境自选）。这是产品决策——agent 自主选环境 vs 用户切后端。

若做，对接 nova 而非 codex：

```
codex：Environment ←→ codex config 体系（~/.codex 层栈，配置在执行现场）
我们：  Environment ←→ nova 体系（settings/trust 归客户端，策略随请求带）
```

子项：

- **环境注册表**：nova server 侧（RuntimeManager 对位 ThreadManager 时，环境注册表归它）——环境 = executor 端点（local/WS/SSH）
- **LLM 环境感知**：工具加 `environment_id` 参数（codex exec_command 对位）+ 上下文注入学 codex 的 `<environment_context>` diff 注入（per-turn user 块，变化才注入——替代/补充我们的 refresh_system_prompt）
- **环境配置**：归 nova settings/trust（客户端）——不做 environmentConfig/read（远程零配置定位不变）
- **远程资源回读**：codex 的 SkillFileSystemsByPath（环境 fs 双通道：执行+资源）——远程项目的 .nova 资源经 executor fs 读回。资源加载层改走当前后端 fs

### 层 3：云托管（暂挂——无场景）

- 远程注册鉴权（Agent Identity/登录态）——codex 云托管产物
- Noise registry 环境供给握手——同上
- 有云端托管场景时再启用

## 不补（定位差异，保持）

- capabilityRoots/discoverV1 + capability_discovery——nova 包体系管能力，executor 不管
- otel 会话遥测——executor 不管会话
- environmentConfig/read——远程零配置定位

## 我们的增量（保留）

fs/readStream 服务端推送 / protocolVersion 版本协商 / WS 入站 bearer 鉴权 / HTTP 白名单+审计 / 独立 CLI / PROTOCOL.md + Python SDK

## 实施序

```
阶段 0（现在）：层 0 四硬伤——executor 立即干净
阶段 1（随后）：层 1 执行层（PDEATHSIG + 沙箱测试恢复 + readStream Rust 封装）
阶段 2（战略决策后）：层 2 环境层（对接 nova——产品决策先行）
阶段 3（暂挂）：层 3 云托管
```

## 验收

- 阶段 0/1：`cargo build --workspace` + `cargo test --workspace`（含恢复的测试）+ Windows 目标编译验证
- 阶段 2：双环境不串味（local+远程并存，LLM environment_id 自选正确路由）+ 远程资源回读金标

---

## 附：控制面/数据面分连接方案（executor 深化批）

> 动机：单连接 FIFO 下，用户文件操作（readStream 大文件）的大帧抢占 LLM 工具通道
> 的实时帧（delta）——用户点开大文件时 LLM 流式卡片一卡一卡。
> WS 单连接是串行单流（无多路复用），解法只有分连接（同端口两条连接，零新端口）。

### 连接划分

| 连接 | 承载 | 帧特征 |
|---|---|---|
| **控制面**（现有 WS） | process 族 + LLM fs（readFile/readBlock）+ environment + http + 全部通知 | 小帧高频——实时 |
| **数据面**（新增第二条连接） | readStream + **writeStream（新增）**——只放大文件流式 | 大流量——随便推 |

划分标准：**调用方**（不按方法贴面标签）——LLM 调的走控制面，用户调的
走数据面。大文件流式（readStream/writeStream）只有用户场景调，自然归数据面；
readDirectory/walk 这类两方都用的方法不分裂——LLM 调走控制面、用户调走数据面，
同方法不同连接按调用源路由（nova 侧本来就知道调用来自工具还是 UI）。

### 协议层

- 新增 `fs/writeStream`（客户端分片推 + 服务端确认通知）——与 readStream 对称
- 方法面归属标注（协议常量或能力位——客户端据此路由）
- 数据面方法集：readStream（已有）+ writeStream（新）

### 传输层

- **同一 WS 监听端口，两条连接**（零新端口——一扇门进两条连接）
- 数据面连接鉴权同控制面（bearer token 共享）
- SSH 形态：我们的 SSH 模式是 `ssh -L` 端口转发承载 WS（远程 executor 听回环）——分连接 = 隧道里两条 WS 连接（SSH 侧零新增：一条隧道两条 WS，零新端口零新进程）

### executor server

- 两连接共享会话状态（进程/fs 状态 server 侧，连接注册表已有）
- fs/writeStream 服务端实现（分片收 + 落盘 + 确认通知）

### 客户端（nova-executor-client）

- `ExecutorClient` 持两条连接（control + data）
- readStream/writeStream/目录浏览路由 data 连接，其余 control
- 数据面连接失败/不支持 → 回退单连接（现状，兼容）

### 帧大小自适应（顺带）

- readStream/writeStream 的 chunk 大小按链路带宽调（低带宽切更小——64KB 级），
  不再固定 1MB

### 实施步骤

```
A 协议：PROTOCOL.md 补 writeStream + 面归属约定；方法面归属标注
B server：多连接会话共享 + fs/writeStream 服务端
C SDK：双连接 + 数据面路由
D nova 侧：文件浏览/同步走 data 连接
```

### 验收

- **金标**：用户点开远程大文件浏览（readStream 拉）时，LLM 正在跑的工具的
  流式卡片 delta 更新**不卡顿**
- 回退：数据面连接不可用时单连接正常工作（旧行为兼容）
- `cargo build/test --workspace` 全绿 + SDK 测试绿

### 沙箱策略（全方法覆盖）

- LLM 工具（process/fs）：平台沙箱（已有）
- 用户操作（readDirectory/walk/readFile/readBlock）：带沙箱上下文 →
  spawn fs_helper 沙箱化子进程（现有 run_sandboxed 机制）——用户也不越界
- readStream：改为 spawn 长命 fs_helper 沙箱化子进程持续读+推（当前实现
  走了 executor 自读的捷径——技术上可沙箱化，补这个实现即全方法沙箱覆盖）

### 挂账联动

- writeStream 补全（读有流式写没有——对称补全）
- readStream 的 nova 侧工作区范围校验（纵深防御——零 executor 改动，nova 侧拦）
