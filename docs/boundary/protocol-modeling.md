# Nova 协议层建模标准

> 坐标：边界·线上契约（体系总图与状态三档约定见 `docs/README.md`）。

> 协议层 = 后端 ↔ 前端（TUI/Web/将来任何语言客户端）之间的线上契约与其演化纪律。本文是这层建模的**总图与单一约定点**。
> 分工：`docs/crosscutting/data-modeling.md` 管类型住所与表示选型（协议层类型同样遵守）；`docs/crosscutting/diagnostics-modeling.md` 管错误/警告/诊断/日志/遥测的诊断与观测家族；`docs/crosscutting/testing-modeling.md` 管测试通用标准；**本文管协议层的分层、正典源、握手、演化、线上错误、传输、事件/条目、生成物与协议测试**。
> 骨架以 codex 三层协议栈（`protocol` 词汇枢纽 → `app-server-protocol` 边界契约 → `app-server` + 独立传输 crate）为第一参考。
> 状态：**目标态定稿**。【现状】= 已建成；【待补】= 设计已定、排期未做。

**总纲**：三层切分、依赖单向——**词汇枢纽（`nova_protocol`，零行为零 JSON-RPC 概念）→ 边界协议包（`nova_server/protocol`：信封 + 方法形状 + 线上词汇 + schema 导出）→ 行为服务器（`nova_server`：分派/状态/桥接）+ 独立传输层（`nova_server/transport`）**。线上契约的一切事实各有一个正典源，演化走机制不走约定。

## 一、分层与目录组织

### 三层职责（codex 对位）

| 层 | codex | nova | 纪律 |
|---|---|---|---|
| 词汇枢纽 | `codex-protocol`（README 明文"minimal dependencies、无业务逻辑"） | `nova_protocol`（纯洁性审计机械执法） | 零行为、零 JSON-RPC 概念——信封/方法表/传输概念**不得**进枢纽 |
| 边界协议包 | `codex-app-server-protocol`（信封 + 方法宏表 + v1/v2 payload + 投影 + schema 导出） | `nova_server/protocol/`（`jsonrpc.py` 信封 + `methods/shapes.py` 形状 + `serialize.py` 事件桥 + `schema_export.py` 导出 + `types/items.py` 线上 item） | 一个**表面**一个协议包：JSON-RPC over stdio/WS 是当前唯一表面；将来 exec-server/MCP 等新表面各立协议包与版本线，演进互不阻塞 |
| 行为服务器 | `codex-app-server`（分派/会话状态/桥接） | `nova_server`（`server.py`/`connection.py`/`reduction/`） | 协议语义在这里终止——handler 不认识信封 |
| 传输 | `codex-app-server-transport`（独立 crate，`TransportEvent` 三变体：连接开/关/入站消息） | `nova_server/transport/`（`base`/`stdio`/`websocket`/`memory`）【现状】 | 传输对服务器只暴露连接事件与写口；in-process 测试传输与真实传输同构 |

**目录组织红线**：传输不进协议包；协议包不进服务器业务；服务器不直接向 socket 写字节（一切出站经信封与传输）。

## 二、单一正典源：方法形状

【现状】`methods/shapes.py` + `MethodRegistry`——**签名即契约**：handler 类型化签名（`async def x(params: XParams) -> XResult`）经 `get_type_hints` 推导形状，三方共用一份声明：

1. **分派校验**：dispatch 前 params 模型校验（缺参/类型错误 → `INVALID_PARAMS`）；
2. **schema 导出**：`schema_export` 汇总为 `methods` 根供 codegen；
3. **能力位宣告**：`initialize` 按域聚合返回。

codex 对位：`common.rs` 一张宏表（157 条 client 方法 + 11 条 server 方法 + 82 条通知）生成 tagged union、`method_name()`、serde tag 分派、响应 payload、experimental 门禁、schema 导出清单。**同一条纪律：加一个方法只有一处事实**——handler 散落处不得手拼 method 字符串、不得出现 per-method if/elif 分派 switch。

【待补】**串行化域声明**：codex 每方法声明 `serialization: thread_id(params.thread_id)` / `global("config")` / `global_shared_read("config")`，由通用排队机消费（per-key FIFO 独占、shared-read 批量并发、read 不插队 write、连接关闭时排队任务经排水闸丢弃）。我们按需在 shapes 加 `serialization` 字段 + `nova_server` 排队机——"同会话的 turn/start 与 turn/interrupt 有序、不同会话并行"应成为基础设施而非各 handler 的活。

## 三、线上词汇：信封 / 事件 / 条目

**信封**【现状】`jsonrpc.py` 的 `JsonRpcMessage`——哑容器（数据建模规则 7）：不校验、不重建，序列化推迟到出货。标准 JSON-RPC 2.0 信封（codex 刻意去掉 `"jsonrpc": "2.0"` 字段走非标；我们保留标准字段——两派都成立，不折腾）。

**事件**【现状】`AgentEvent` 判别联合（住 `nova_protocol/agent_events.py`）+ `serialize.py` **哑管道直通**（`{type, data}` 信封，零呈现加工——前端自行映射渲染模型；唯一加工是 JSON 安全兜底）。codex 同款：core 事件枚举 + 投影集中。

**条目**【现状】`types/items.py` 的 `WireItem` 开放集：框架六变体判别联合 + `CustomItem` 兜底（`type` 开放字符串 + 额外字段透传）。**开放集纪律**（codex 同款）：不给主联合加 `Unknown` 变体（未知 type 直接校验失败）；第三方词汇走**命名空间子通道**——包级 item 的 `type` 必须带包命名空间前缀（`<包名>.<类型名>`，codex 的 `ExtensionItem.kind = "<namespace>.<kind>"` 对位）。

**投影纪律**（codex `event_mapping.rs` 纯函数 + 一处副作用处理器的对位）：core→线上的翻译集中一处（我们的 `reduction/`）；**显式吞掉必须写理由**（codex 对 PatchApplyBegin/End 的空 arm 注释是范本）；live 通知与历史重建**共用同一构造器**（codex `item_builders.rs` 两路共享防漂移——我们的 reduction 同此纪律）。

## 四、握手与能力协商

【现状】`initialize` 返回 `{version, contract_version_major, contract_version_minor, capabilities: {domains, methods}}`——major 不等硬拒、minor 加法放行。

codex 对位（`v1.rs`）：`InitializeParams{client_info, capabilities}`——`client_info{name, title, version}` 必带；capabilities 按位声明（`experimental_api`、通知退订 `opt_out_notification_methods` 等）；**握手前任何非 initialize 请求一律拒绝**（`"Not initialized"`）；握手完成后才向该连接开放广播。

【待补】

- **ClientInfo**：`initialize` 入参收 `{name, title, version}`——客户端身份是后续豁免/遥测/兼容决策的依据；
- **通知退订**：按方法名退订通知（codex `opt_out_notification_methods`），出站侧按连接过滤；
- **experimental 三级门禁**（见下节）。

## 五、演化纪律

1. **minor / major**【现状】：加 optional 字段/加联合成员/加方法 = minor；删字段/改语义/改判别键/可选改必填 = major（升 `contract_version_major`，记 CHANGELOG）。老客户端缺字段永远能初始化（`serde(default)` 对位：pydantic 默认值）。
2. **experimental 三级门禁**【待补】：实验面引入主分支而不成事实承诺——
   - **注解层**：pydantic `Field(json_schema_extra={"experimental": "<方法或原因>"})`；
   - **导出层**：`schema_export` 产稳定版时剪掉实验项（实验面另出一份）；
   - **运行时层**：握手声明 `experimental_api` capability 才放行——入站未授权用实验方法即拒（`INVALID_REQUEST`），出站实验字段/通知按连接剥掉。
3. **deprecationNotice 一等通知**【现状】：`MethodRegistry.register(..., deprecated="迁移指引")` 标记弃用；服务器分派前先发 `system/deprecationNotice` 通知（`{method, summary, details}`）再响应——迁移窗口内客户端可见（测试钉住"先发通知"语义 + 未弃用不掺通知）。schema 导出的 `@deprecated` JSDoc 标注与第一方豁免（按 ClientInfo 不骚扰自家老客户端）归后续波次。
4. **v1 冻结 / v2 命名空间**【将来】：真到不兼容大版本时采用——旧版结构性冻结（改动需过 allowlist 的有意摩擦），新版按目录/命名空间自包含导出。
5. **开放集兜底**【现状】：见 §三——`CustomItem` 吸收未知 item；`extra="allow"` 基座保数据不丢。

## 六、线上错误

**请求级**【现状】`protocol/errors.py` 的 `JSONRPCError`：标准码（PARSE_ERROR/INVALID_REQUEST/METHOD_NOT_FOUND/INVALID_PARAMS/INTERNAL_ERROR）+ 自定义码（NO_ACTIVE_SESSION/SESSION_NOT_FOUND/MODEL_NOT_FOUND/SESSION_IN_USE/OVERLOADED/REQUEST_CANCELLED）。纪律（codex 同款）：

- **标准码为主，自定义数字码克制**（落在 -32099..-32000 保留区间，一个码一个语义，注释写理由与码位避让）；
- **机器可读的细分放 `data`**：结构化错误码用字符串键放 `data`（codex 的 `data: {"input_error_code": "input_too_large", ...}` 模式）——【待补】`data.errorKind` 挂 `NovaErrorKind`（docs/crosscutting/diagnostics-modeling.md 已定设计）+ data 全等断言测试。

**事件级**【现状】turn/流内错误**不复用请求级信封**——`AssistantMessage.error_kind`（终态，随消息落盘）+ `ErrorEvent` 直通 + `WarningEvent`（非致命，词汇就位）。瞬态重试中走 `AutoRetryStart/EndEvent`（codex `StreamError` + `will_retry` 语义对位——**不立独立 StreamErrorEvent**）。

**分工一句话**：请求级拒"这一个调用"，事件级报"这一程运行"。

## 七、传输与双向原语

【现状】`transport/`：`base`/`stdio`（NDJSON）/`websocket`/`memory`（测试）——服务器只消费连接事件；入站背压即回 `OVERLOADED`（-32004，codex -32001 对位）。

**双向请求（server→client）**【现状】`ui_context.py` 泛型 transport（`capabilities`/`has_capability`/`request`/`notify`，词汇定义权归包——审批/选择/输入等反向原语）。

codex 对位的成熟形态（**待补方向**）：反向请求与正向**并列但分离**的方法表；**独立 id 空间**（服务器单调计数器，防与客户端自选 id 碰撞）；`id → pending 回调`注册表按会话作用域记账；**生命周期清理**——turn 开始/结束时作废挂起请求（附结构化 reason）；多客户端竞答经 `serverRequest/resolved` 通知协调；新连接接入可重放挂起请求。我们的 `ui_context` 向此演进时按此清单验收。

## 八、生成物与代码生成

【现状】`schema_export.py` 构建期导出 `nova-wire.schema.json` + `nova-wire.gen.ts`（入库），pytest 漂移测试保鲜，重生命令一行（`python -m nova_server.protocol.schema_export`）；**生成物 lint 测试**（`tests/conformance/test_wire_lint.py`）机械执法三条线上约定：camelCase 键、事件必有 `type` 判别字段、模型变体联合必有 `discriminator`（开放集豁免）；walker 发射 `discriminator`（闭联合——codex serde tag 对位）。

【待补】
- **check-clean-worktree**：CI 步骤后工作树脏即红（抓"改了类型忘了重生 schema"）；
- **版本内嵌导出**：schema 作为包数据随 wheel（`importlib.resources` 已就位）+ `nova-pkg schema dump` 子命令——"这份 schema 对应哪个版本"零歧义（codex zstd 内嵌 + `generate-ts` 子命令对位，压缩不学）。

## 九、协议测试

【现状】`nova_server/tests/conformance/`（漂移测试）+ 方法级测试。补强方向（codex `app-server-protocol` 内联测试 + `app-server/tests/suite/v2/` 119 文件对位）：

1. **金标 roundtrip**：完整 JSON 字面量 + 全对象相等（不许字段级抽样）——判别联合重建是重点（防剥子类字段）；
2. **兼容反序列化**：老数据能读（alias/default/兜底变体各一例——`#[serde(other)]` 只用于标量枚举的纪律对位）；
3. **协议行为契约测试**：每个方法/资源一个文件，断言**通知序列与载荷**（item/started→completed→turn/completed 式）、握手契约（重复 initialize 拒绝、非法 client name 拒绝、退订生效）、experimental 门禁（未声明 capability 调实验方法 → 拒绝 + 固定文案）、deprecated 先发通知（全等断言）；
4. **TS 消费形态保鲜**：`nova-wire.gen.ts` 变更必须伴随前端类型检查通过（生成物即契约，PR 里可见）。

## 十、与其他文档的分工

| 问题 | 去哪看 |
|---|---|
| 新类型住哪、用 Pydantic 还是 dataclass | `docs/crosscutting/data-modeling.md` |
| 线上契约怎么组织/演化/握手/测试 | **本文** |
| 错误/警告/诊断/日志/遥测怎么建模 | `docs/crosscutting/diagnostics-modeling.md` |
| 测试怎么写（分类/住所/假缝/断言） | `docs/crosscutting/testing-modeling.md` |

边界一句话：协议层的**形状**遵守数据建模标准，协议层的**诊断**遵守诊断与可观测性建模标准，协议层的**测试**遵守测试建模标准——本文只管协议层**自身的组织与演化纪律**。

## 十一、现状与待补清单

| 项 | 状态 |
|---|---|
| 三层切分 + 依赖单向 | 【现状】（纯洁性审计执法） |
| 方法形状单一正典源（签名即契约，三方共用） | 【现状】 |
| 信封哑容器 / 事件哑管道 / WireItem 开放集 | 【现状】 |
| initialize major/minor 握手 | 【现状】 |
| 请求级错误码（标准 + 6 自定义） | 【现状】 |
| 事件级错误（error_kind 上消息） | 【现状】 |
| schema 入库 + 漂移测试 + 重生命令 | 【现状】 |
| **生成物 lint 测试**（camelCase/事件 type/联合判别键） | 【现状】 |
| **联合判别键发射**（walker 发射 discriminator，闭联合全覆盖） | 【现状】 |
| **deprecationNotice 发送纪律**（register 标记 + 分派发通知 + 测试） | 【现状】 |
| 串行化域 + 通用排队机 | 【待补】 |
| ClientInfo + 通知退订 | 【待补】 |
| experimental 三级门禁 | 【待补】 |
| `data.errorKind` | 【待补】（diagnostics-modeling 波次 5） |
| check-clean-worktree | 【待补】（CI 步骤弄脏工作树即红） |
| 版本内嵌导出 + schema dump 子命令 | 【待补】 |
| 反向请求成熟形态（独立 id 空间/生命周期清理/竞答通知） | 【待补】 |
| 协议行为契约测试套件（通知序列/握手/门禁断言） | 【待补】 |
| schema 导出的 deprecated JSDoc 标注 + 第一方豁免 | 【待补】（deprecationNotice 已落地后的演化项） |
