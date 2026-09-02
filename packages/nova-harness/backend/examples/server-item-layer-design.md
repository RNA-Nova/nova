# Nova 最终版 server 层设计（开放统一 item 层 + 归约归服务器）

> 状态：**设计定稿（待实施）**。本文档是 2026-08 架构讨论（三方实证对比 pi/codex/nova）的落地图纸。
> **关键前提：本项目尚未发布**——无旧客户端、无存量会话、无兼容包袱。因此：契约直接以本设计为
> **1.0 起步**（item 契约即首个公开契约，无过渡周期/废弃标记/迁移代码）；旧事件词汇直接删除
> 不留兼容层；mirror 直接重写；分阶段不设"先导停靠点"，一口气到终态。
> 前置阅读：`event-vocabulary-comparison.md`（三家词汇穷尽对照）、`nova_architecture_2.0.md`（三层模型，文首修订已翻案 WS 归属）。
> 实证基线：RPC 连接化 P0–P3 已落地（1498 测试全绿、conformance 双传输 10/10、PTY smoke 45/45）。

---

## 1. 设计目标与公理

把 server 层升级为 codex 对齐的终态：**开放统一 item 层 + 归约归服务器**。五条公理（全部经三方实证打磨）：

1. **根本治理 > 兜底**——每个故障类追到前提消灭之，不加兜底层；
2. **发生系 vs 状态系判据**——"迟到的客户端需要看到它现在的样子吗？"需要则实体（状态系），不需要则信号（发生系）；
3. **归约归服务器 = 语义模型归服务器**——实时/恢复/多端/SDK 同吃一份归约成品；
4. **开放性 = 注册表不是枚举**——统一模型与开放生态不冲突；
5. **按留痕价值分形态**——审批即数据、问答即浮层、调用即宿主、终结归仲裁。

## 2. 现状基线（已建成）

RPC 连接化 P0–P3（`core/rpc/`，实证）：

- `connection.py`：Connection 一等公民（进程级 id、initialized 状态机、按连接 UI 能力集、在飞请求表、有界出站队列 + 独立写泵、背压按来源分流——stdio/memory 阻塞等位、WS 慢消费者断连）；
- `server.py`：`RpcServer` 连接注册表 + 每连接读泵、事件广播 + initialize 门、信封 `seq/ts/sessionId` 打戳（P2 锚点）；
- `ui_context.py`：`RoutingUIContext`——发起方优先（contextvar）/广播首响应胜出 + 败者 `ui/cancel`、断连按 cancelled 收尾；
- `transport/websocket.py`：WS 传输 + acceptor + 鉴权三守则（bearer 常数时间比较、非 loopback 无显式 token 拒启、Origin 白名单 403）；
- `syncSession` 原子快照（状态 + 条目分页 + 事件高水位）；入站在飞上限 `-32004`；loop-lag 探针；
- conformance 黑盒套件双传输（stdio + WS）参数化。

本设计在其上叠加：**归约层（新增）+ item 层（新增）+ 挂起生命周期收口 + 布局归位**。

## 3. 终态分层图

```
nova_agent（纯 loop：产消息 + AgentEvent——零改动）
   ↓ 原始事件（内部母语层：落盘/扩展继续消费，照常）
nova_harness
   ├── 会话状态（消息形 JSONL 落盘——照旧，LLM 上下文零转换保留）
   ├── 【新增】归约层（有状态翻译器：事件→item 迁移 / 条目→item 清单）
   │     ├── item_mapping.py（纯映射，表格驱动易测）
   │     └── 状态编排器（在飞 item 状态机，贴会话事件总线）
   └── rpc/（接入层：连接注册表扇出 item 通知）
        ↓ 线上：item/started|delta|completed + 域通知 + seq/ts/sessionId
客户端（TUI/web/桌面）：mirror 退化为 apply 容器 + 渲染槽
```

**关键澄清（修正过）**：落盘保持**消息形**——理由是 **LLM 上下文热路径零转换**（每次调模型都跑的路径不搁翻译层；恢复读的"条目→item"翻译只在恢复时跑一次，冷路径），**与兼容性无关**（未发布，无旧会话包袱——这个理由不靠它）。item 是纯"线上/呈现层"实体形状。codex 同构（rollout 落 core item/ResponseItem 形，线上 ThreadItem 呈现形，`history/src/lib.rs:30-39` 在案）。**不存在 LLM 翻译债**——翻译只在"LLM 形 ↔ 呈现形"之间、服务器侧。

## 4. item 层设计

### 4.1 骨架

```python
class NovaItem(NovaBaseModel):
    id: str                # 稳定身份（创建即分配，一贯到底）
    type: str              # 判别符（框架变体 + 包注册变体；Field(discriminator="type")）
    status: str | None     # pending/running/done/failed/declined/cancelled
    source: str | None     # 来源分源：agent/user（用户 !cmd 与 LLM 工具同骨架）
    ts: int                # 创建时刻（epoch ms）
    # + 变体字段（pydantic 判别联合）
```

### 4.2 变体两源（开放性所在）

**框架变体**（协议内置——LLM 会话的固有形状，不是能力）：

| 变体 | 语义 | 关键字段 |
|---|---|---|
| `UserMessage` | 用户消息 | content（text/image blocks） |
| `AgentMessage` | 助手文本 | text |
| `Thinking` | 推理/思考 | summary/content |
| `ToolCall` | **LLM 工具调用（一等实体）** | tool/args/status/result?/duration_ms?/error? |
| `Compaction` | 压缩标记 | reason/summary 引用 |
| `BranchSummary` | 分支摘要 | summary/from_id |

**包级变体**（`MESSAGE_TYPES` 升格为 item 注册表——开放性载体）：

| 变体 | 包 | 语义 |
|---|---|---|
| `BashExecution` | nova-coding-agent | 用户 `!cmd` 执行（command/output/exit_code/cancelled/truncated/full_output_path/exclude_from_context） |
| `PermissionDecision` | nova-coding-agent | 审批留痕（tool/命令/decision/时间/归属 run） |
| 任意第三方 | 任意包 | 注册即变体 |

> **注册面注意（实施时必修）**：`MESSAGE_TYPES` 注册表现状只挂 user_tools loader
> （`loaders/user_tools.py:155`，函数名 `register_user_tool_message_types`）——
> 扩展没有注册面，而 `PermissionDecision` 恰好是扩展（permission_gate）的产物。
> 落地前必须把注册面升格为**包级通用**：扩展经 `NovaExtensionAPI` 加
> `register_message_types([...])`（与 on/registerCommand/registerShortcut 同族的
> 声明式注册），工具/用户工具继续走 `MESSAGE_TYPES` 类属性约定；注册表改名
> 去 user_tool 化（`register_message_types`）。机制（role 键/幂等/碰撞警告/
> 包缺席降级 Opaque）全部现成复用，只扩入口。

**界线**（理念不破）：协议骨架归框架（消息/工具调用/压缩——LLM 会话固有形状），能力内容永远包注册。codex 把能力形状（WebSearch/ImageGeneration）也内置 18 变体里；我们的界线更靠外——`ToolCall` 骨架归框架、bash 渲染器归包，互不越界。

### 4.3 生命周期

```
started（创建即真身，携带初始状态）→ delta（瞬态增量，不落盘）→ completed（终态，落盘点）
```

- 一次性内容（无流式无 pending）生命周期退化：`started + completed` 连发（现状 `record()` 的 MessageStart+End 连发即此形态）；
- **落盘卫生**：delta 瞬态不落盘；completed 才落盘（JSONL 一次执行一条记录）；
- **中断语义**：run abort 时在飞 item 按 `cancelled` 定稿（仲裁触发——见 §6）。

### 4.4 管理信息不进 item

leaf/label/settings 变更/角色切换归**元数据层/域通知**（学 codex：item 只管"会话内容"）。

## 5. 归约层设计（核心新件）

### 5.1 纯映射（`rpc/protocol/item_mapping.py`，无状态、表格驱动）

- **事件→迁移**：`tool_execution_start{tc1,args}` → `item/started{ToolCall tc1,status:running}`；`tool_execution_update{partial}` → `item/delta{tc1,partial}`；`tool_execution_end{result,isError}` → `item/completed{tc1,status:done|failed,result}`；`message_start/update/end` → AgentMessage item 对应迁移；user_tool start/chunk → BashExecution item 迁移（user_tool 事件组消亡）；
- **条目→item**（恢复读）：AssistantMessage 里的 ToolCall 块 + 配对 ToolResultMessage → 合并为 ToolCall item（status 推断：有结果=done，无结果=cancelled——中断语义在此定义）；
- **快照→delta**：message_update 的全量快照序列 → 记上次长度取后缀产 `item/delta` 增量序列。

### 5.2 状态编排器（harness 侧，贴会话状态）

- 持有"在飞 item 状态机"（started 未 completed 的 item 各一份）；
- 订阅 session 事件总线（内部母语层），产 item 通知交给 `RpcServer.broadcast`；
- 恢复路径：syncSession 时把会话条目经纯映射翻译成 item 清单。

### 5.3 同形性验收（关键性质）

**同一会话，实时事件流产出的 item 清单 == 恢复读产出的 item 清单**——写成金标测试，这是"恢复与实时同形"的机器证明（codex 的 rollout↔wire 同构的对应验收）。

### 5.4 住所

- 纯映射：`rpc/protocol/item_mapping.py`（类型旁边，codex `app-server-protocol/.../event_mapping.rs:30` 对位）；
- 状态编排器：`core/harness/reduction/`（新模块，贴会话状态——归约要读 session 内容）。

## 6. 线上词汇变化

| | 现状（Bus 2，26 型直通） | 终态 |
|---|---|---|
| 内容事件 | `message_*`/`tool_execution_*`/`user_tool`/`entry_appended` | **消亡**——归约层消费后产 item 通知 |
| 新增 | — | `item/started`/`item/delta`/`item/completed`（信封 seq/ts/sessionId 沿用） |
| 域通知 | 环境类（session_info_changed/queue_update/auto_retry_*/compaction_*/session_replaced/cache_miss/extension_error/thinking_level_changed/model_changed/session_reloaded） | **保留直通**（发生系——迟到者不需要看到"曾经重试过"） |
| 作用域标记 | agent_start/end、turn_start/end | **保留**（仲裁清扫点） |
| 恢复读 | getSessionEntries（条目）+ getSessionState | syncSession 返回 **item 清单 + 水位**（转录恢复走 item）；**条目图接口保留**——理由是树导航载体（fork/navigateTree 按条目 id、parent_id 链住在条目上），不是兼容兜底（前后端锁步发版 + major 硬拒，无旧客户端需要过渡） |

契约**重置为 1.0 起步**（item 契约即首个公开契约——此前 1.x 从未发布，无旧客户端需要兼容；旧事件词汇直接删除，不留废弃标记/过渡周期）。

## 7. 挂起请求生命周期（根本治理版）

- **作用域归属**：每条 `ui/request` 记 `run:<id>`/`session:<id>`/`global` 归属进 `RoutingUIContext` 台账；归属经 contextvar 织入（`current_connection` 同款加 `current_run`；run id 在 agent_start 生成携带）；
- **仲裁清扫**：`RoutingUIContext.cancel_scope(scope)`——按归属批量终结（协程 cancelled + 按台账 addressed 集发 `ui/cancel`）；挂接点：server 对 `agent_end`/`session_replaced` 调它（codex `cancel_requests_for_thread` 直译，`outgoing_message.rs:478`）；
- **超时**：删 300s 全局默认（永等，pi/codex 对齐）；`timeout_ms` per-request 保留为业务语义（OAuth 授权链接会过期），**到点必须配撤框**（cancelled + `ui/cancel`）；
- **竞速退场**：`_watch_abort` + ScopedUIContext signal 织入删除（仲裁覆盖后冗余）；**保留** CancelledError 路径——它与仲裁**不重叠不冗余**：cancelRequest 是"客户端主动取消单个调用"（LSP 式，即时响应），仲裁管"宿主死亡"（run/session 终结批量清扫）；两者可能同事件触发，幂等并存（done/popped 双检查）。
- **多端**：发起方优先；无归属广播 + 首响应胜出 + 败者 `ui/cancel`；
- **重连重放**：挂起归会话不归连接；新连接 initialize 后原帧重放（台账存原始帧——codex 克隆体重放对位，`outgoing_message.rs:362`）；前端 WireClient 加重连 + initialize + syncSession 水位对账。**机型注意**：stdio 单客户端下重放无意义（连接死即进程退，exit_on_close）——台账归属在本批就位，重放启用归 WS/桌面端批次；
- **水位对账的两条客户端纪律**（S3/WS 批实施时必须写上，缺一就有洞）：①**重连重置水位**——seq 是服务器生命周期序号，进程重启归零，客户端每个新连接以本次 syncSession 的水位为准（旧水位不得跨连接沿用）；②**同步前缓冲**——重连至首个 syncSession 完成之间收到的事件先缓冲不应用，快照到手后缓冲里 `seq ≤ 水位` 丢弃、`> 水位` 按序应用（否则窗口期事件既在快照里又在线上来，双应用）；
- **能力消失自答**（前端）：包 reload 卸了 dialog slot 时，在飞该 slot 请求自动应答 cancelled。

**顺序硬依赖**：先仲裁、后删超时（否则忘织 signal 的调用点从挂 300s 变挂永久——退步）。

## 8. 三分流（按"谁在中间"）

| 交互 | 通道 | 获得 |
|---|---|---|
| **审批**（permission_gate 类） | **PermissionDecision item**（pending 建 → 应答 update 定稿；弹窗照弹——dialog 负责问，item 负责记） | 事实化/审计/重连恢复/多端可见——纯包侧改动 |
| **临时问答**（question/trust/OAuth 引导） | `dialog:*` 浮层（现状保留） | 雁过无痕，合理 |
| **执行调用**（打开链接/通知/剪贴板） | **`host:*` 宿主原语族**（即发即答，无 UI 生命周期负担） | 执行位置永远在客户端 |

**`host:*` 命名已修正**（原 `client:*`——client 是全集；host 与 ExtensionUIAPI 的"宿主原语"汇合）。首发 `host:openUrl`：auth 流程改"客户端开"（TUI 宿主注册能力 spawn open/xdg-open；web 端 window.open；无能力/print 模式显示 URL——显示是主通道不是兜底；webbrowser.open 只在"后端即用户环境"的 print/CLI 合法）。

## 9. 客户端变化

- **mirror**：mapping.ts 归约逻辑全灭（openToolCalls/openUserTools/callId 桥/streamingEntryId/类型猜测）；store 保留四样——镜像副本（item 清单+快照+水位）、本地视图状态（折叠/滚动/草稿）、轻量派生态（loader/通知）、响应式枢纽；
- **渲染器**：工具渲染器输入从 ToolCallCard 换 ToolCall item（rename 级）；`entry:<type>` 槽不变（BashExecutionCard 继续吃 item 数据）；
- **转录重建**：syncSession → item 清单直接渲染（不再从消息条目重建）；
- **codex 对照**：它的客户端 store 实证（App/ChatWidget 结构体字段）保留 pending 登记/幂等簿记/视图状态——归约簿记死、机制簿记永存。

## 10. 布局归位（先行半步）

```
nova_harness/
├── core/            # 业务核心（含新增 reduction/）
├── rpc/             # ← 从 core/rpc/ 挪出（接入层与 core 平级）
├── package/         # ← 从 core/package/ 挪出（生态管理与 core 平级）
├── modes/、cli/     # 不动
```

理由：core=业务核心、rpc=接入层、package=生态管理——目录语义与 codex 的 crate 边界对齐（实测依赖已单向：harness 主体对 core/rpc 零感知）。已知坑：`schema_export._default_repo_root()` 按 `__file__` 深度推仓库根，挪浅一层要跟着调。

## 11. 包生态与开放性

- **开放性零损失**：item 变体 = MESSAGE_TYPES 注册表（注册表不是枚举）；渲染器 = entry 槽；上下文翻译 = ContextInjectable 协议（每变体自供 to_context_text）——全部包级；
- **框架零内置不破**：内置的只是协议骨架变体（消息/工具调用/压缩），能力内容永归包；
- **学 codex 补治理**（另一项目）：marketplace（源与包分离+持续同步）、版本目录缓存、原子替换、策略门、hook 哈希式细粒度信任；
- **不学零代码哲学**：我们的扩展是进程内 Python 代码（能力面选择），生态治理做厚来配它。

## 12. 三方对比（终态 vs 现状 vs codex vs pi）

| 维度 | pi | codex | 我们现状 | **本设计（终态）** |
|---|---|---|---|---|
| 实体模型 | 消息+custom（无执行实体） | ThreadItem 18 封闭枚举 | 消息嵌套+卡片运行时重建 | **开放统一 item 层**（框架变体+包注册表） |
| 事件翻译层 | 无（进程内） | **服务器侧** | 客户端 mirror | **服务器侧**（mirror 退化为容器） |
| 事件序号 | — | 无 | **seq/ts/sessionId** | 同左（保留领先） |
| 交互词汇 | 进程内 28 件 | 封闭 11 类型化 | 开放泛型+能力降级 | 同现状（两族定型） |
| 挂起终结 | race | 中央仲裁 | 竞速+300s 兜底 | **作用域仲裁** |
| 超时 | 无全局 | 无全局（login 10min 业务） | 300s 全局 | **无全局+timeout_ms 业务语义** |
| 撤框 | race 免费 | resolved 广播+turn 事件 | cancel 帧散点 | cancel 帧两点（仲裁+败者） |
| 重连重放 | — | 有 | 无 | **有**（挂起归会话） |
| 用户 `!cmd` | 零事件直调 | item（source=userShell） | user_tool 事件组 | **item 变体（事件组消亡）** |
| 审批 | 浮层无痕 | item（事实化） | 浮层无痕 | **PermissionDecision 条目** |
| 执行调用 | 进程内 | item/tool/call | 无 | **host:* 族** |
| 插件生态 | 散养代码 | 纯资源包+最全治理 | 代码+资源，治理弱 | 代码+资源开放 + 补 codex 式治理 |
| 落盘 | JSONL 消息 | rollout JSONL（core item） | JSONL 消息 | **JSONL 消息照旧（LLM 零转换）** |

## 13. 分阶段实施计划

| 阶段 | 内容 | 验收门 |
|---|---|---|
| **A. 布局归位** | rpc/package 挪平级 | 1498 全绿 + 双传输 + PTY |
| **B. 挂起生命周期收口** | 仲裁 + 删超时 + 竞速退场 + 台账归属（重放启用归 WS/桌面批）+ 前端能力自答 | 仲裁单测 + PTY（弹窗 Esc 即撤） |
| **C1. item 模型** | NovaItem 骨架 + 框架变体 + MESSAGE_TYPES 升格 + schema 导出 | 类型单测 + 漂移测试 |
| **C2. 归约层·实时** | 状态编排器 + 纯映射 + item 通知上线 | 事件序列→迁移序列表格测试 + 同形性金标 |
| **C3. 归约层·恢复** | syncSession 换形（条目→item 清单）+ 中断语义 | 旧会话兼容测试 |
| **D. 客户端退化** | mirror 简化 + 渲染器对齐 + sync 换形 | 前端 357 绿 + PTY smoke 45/45 |
| **E. 三分流** | PermissionDecision 条目 + host:openUrl | PTY 专项（审批留痕/重连恢复/打开链接） |
| **F. 契约 1.0 + 文档** | 以 item 契约为首个公开版本（重置起步） + conformance 换契约 + AGENTS/CHANGELOG | conformance 双传输全绿 |

**实施策略（已拍板：一口气到终态）**：未发布无兼容包袱——不设"先导停靠点"，A→F 一口气走完；中间态只存在于工作分支之间，不存在于发布之间；旧事件词汇直接删除、mirror 直接重写、不写任何迁移/兼容代码。

**工作量粗估**：A 0.5 天 + B 1.5 天 + C1 1 天 + C2 1.5 天 + C3 1 天 + D 2 天 + E 1 天 + F 0.5 天 ≈ **9 天量级**。

## 14. 风险清单

| 风险 | 对策 |
|---|---|
| 归约器状态机错误（中断/异常路径） | 中断语义先定义（abort→cancelled）；同形性金标测试兜底 |
| 流式 delta 带宽/正确性（快照→增量） | 记上次长度取后缀；conformance 校验增量序列拼回等于终态 |
| mirror 大改的回归面 | 前端归约测试整体重写 + PTY 全量矩阵 + 真实 LLM 轮次 |
| 契约断裂期混合版本 | **未发布即无此险**——契约重置 1.0 起步（item 即首个公开契约）；major/minor 门保留，发布后才开始服役 |
| 删超时后漏网调用点挂永久 | 顺序硬依赖（仲裁先上）+ pending 台账超 N 分钟打 stderr **诊断日志**——定位是**开发期可见性**（暴露故障），不是生产兜底（不终结、不掩盖）；仲裁是结构保证，信任它 |
| run id 归属错杀（跨 run 误清扫） | run id 在 agent_start 生成、contextvar 织入；仲裁只按精确归属清 |

## 15. 决策点（已全部拍板）

1. **实施策略**：✅ 一口气到 F（未发布无包袱，不设停靠点）；
2. **中断语义**：✅ `cancelled`——run abort 时在飞 item 按 cancelled 定稿（与 UI 的 cancelled 语义同词，一处语义一处词）；
3. **管理条目切割**：✅ 归元数据层不进 item（学 codex：item 只管会话内容，leaf/label/settings 变更走域通知/元数据）。

## 16. 实证参考清单（本讨论的取证点）

- codex 连接管理：`app-server/src/lib.rs:711`（stdio 单客户端）、`transport/mod.rs:196`（ConnectionId 自增）、`outgoing_message.rs:47`（复合键）、`transport.rs:156-173`（慢消费者断连/stdio 阻塞）；
- codex 挂起仲裁：`outgoing_message.rs:478`（cancel_requests_for_thread）、`:362`（重放）、`thread_lifecycle.rs:806`（serverRequest/resolved 广播）；
- codex WS 鉴权：`app-server-transport/src/transport/auth.rs:283`（常数时间比较）、`websocket.rs:135`（非 loopback 拒启）、`:89`（Origin 403）；
- codex item：`app-server-protocol/src/protocol/v2/item.rs:226`（ThreadItem 18 变体）、`:1031`（CommandExecutionSource 四值）；
- codex 归约：`app-server/src/bespoke_event_handling.rs`、`app-server-protocol/.../event_mapping.rs:30`（纯映射）；
- codex 插件：`core-plugins/src/manager.rs`（PluginsManager）、`plugin/src/provider.rs:30`（inert descriptor）、hooks 哈希信任（`config/src/hook_config.rs:28`）；
- codex login 分裂：`cli/src/login.rs:151`（open_browser:true）vs `app-server/.../account_processor.rs:478`（false + 返回 auth_url）；
- pi：`agent/src/types.ts:415-430`（AgentEvent 10 型）、`interactive-mode.ts:5890`（handleBashCommand 零事件直调）、`agent-session.ts:2742`（recordBashResult 双写）、`core/extensions/types.ts:127-278`（ctx.ui 28 成员）；
- 我们：`core/rpc/connection.py`、`server.py`、`ui_context.py`、`transport/websocket.py`（P0–P3 在案）；`frontend/src/mirror/mapping.ts:463`（callId 桥 wart）；`nova_coding_agent/backend/user_tools/bash.py:138`（start 事件）。
