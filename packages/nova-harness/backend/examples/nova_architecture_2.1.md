# Nova 架构 2.1：从 harness 到前端的完整设计

> 状态：**设计定案**。本文档是架构 2.0（`nova_architecture_2.0.md`，三层模型）落地后的
> 完整描述——从 Python harness 内部结构，穿过线上协议，到 Node 层与前端，
> 含已定案的扩展体系与后端可插拔设计。
> 与 2.0 的关系：2.0 是图纸（三层模型 + 三条宗旨），本文档是施工图与判决记录——
> 2.0 第 1 步（Python 纯运行时 + RPC 补全）已完成，本文档固化其终态，
> 并给出第 2/3 步（Node 层、复合包、多后端）的完整设计。

---

## 0. 一屏总览

```
┌─────────────────────────────────────────────────────────────────┐
│ 前端（TUI / WebUI）        纯渲染 + 输入捕获 + 键位绑定           │
│   TUI 经进程内 API / WebUI 经 WebSocket（M3）                     │
├─────────────────────────────────────────────────────────────────┤
│ nova-client（Node，TS）  呈现的一切                          │
│   rpc-client · mapping（纯函数归约器）· store（推+拉）· facade    │
│   观察式事件总线 · 渲染器注册表 · UI 扩展宿主（M4）                │
├────────── JSON-RPC over stdio（NDJSON，唯一跨进程通道）──────────┤
│ nova_harness（Python）       纯 agent 运行时，零 UI 概念           │
│   agent loop · 工具执行 · 会话树 · compaction · 模型/auth          │
│   扩展（行为钩子）· 包管理 · 资源索引 · RPC 服务（62 方法）        │
└─────────────────────────────────────────────────────────────────┘
```

数据流一句话：**Python 生产事实 → 哑管道直通 → Node 层翻译为可渲染模型 → 前端只画**；
反向一句话：**前端手势翻译为命令 → 哑管道 → Python 执行，键位永不过线**。

---

## 1. nova_harness（Python）：纯 agent 运行时

### 1.1 不可妥协的三条（继承自 2.0）

1. **框架零内置**：不内置任何工具、不预设名单；能力全部来自包。
2. **安装 ≠ 加载**：安装是静态注册，加载是被选择触发的动态行为。
3. **选择有限**：选什么加载什么；不选 = 无能力。

### 1.2 模块地图（`core/`）

| 域 | 内容 |
|---|---|
| `agent_session/` | `AgentSession`（会话 facade）+ 领域控制器（compaction/events/model/queue/retry/slash_input/stats/tools/tree/user_tools）+ runtime（会话切换/分叉重建）+ factory/services（装配） |
| `harness/` | 会话持久化（JSONL 条目树）、compaction/分支摘要、system prompt 构建、skills、project_trust、tools 管理（`ToolsManager`/`DynamicTool`）、user_tools 管理 |
| `resources/` | 资源加载器：索引全量（静态、轻）→ 选择（agent.yaml 白名单）→ 加载按需（动态、重） |
| `package/` | 包管理：`[tool.nova]` 六类目（agents/tools/skills/extensions/prompts/user_tools）、**两种身份证**（pyproject → A 型；包根 package.json → B 型纯 TS 包）、安装五阶段（物化 → pip/uv 依赖 → 自安装 → 二进制 → **npm**（包根 package.json，ci/install，失败解耦））、dist-info 事实记录、解析、二进制依赖三级链 |
| `extensions/` | 扩展系统：loader/runner/API——**纯 agent 行为钩子**（session_start、compaction、project_trust、provider 注册、命令/flag/shortcut handler、spawn hook） |
| `model/` | ModelRuntime：模型注册表三层合成（内置 → models.json → 扩展注册）、auth 联动、login/logout |
| `config/` | settings（全局/项目两级 + 写队列持久化）、auth storage、路径默认值 |
| `rpc/` | 通信层：transport（stdio/memory）← protocol（jsonrpc/router/serialize/schema_export/methods/ui_context）← server（NovaServer 组装） |
| `types/` | 全部跨模块类型（pydantic/dataclass 选型见 AGENTS.md 数据建模节） |
| `utils/` | 遥测（install）、HTTP 空闲超时、二进制解析、子进程跟踪、OutputGuard 等 |

### 1.3 事件总线：三条，各有其名

- **Bus 1（agent 事件）**：`nova_agent` 的 agent loop 事件（agent/turn/message/tool_execution 全生命周期）；
- **Bus 2（AgentSession 事件）**：Bus 1 + 会话层自动事件（auto_compaction/auto_retry/queue_update/model_changed/session_info_changed/user_tool/compaction 起止），共 **21 种**——**RPC 桥的唯一事件源**；
- **Bus 3（扩展事件）**：`ExtensionRunner` 的拦截式事件（before_tool_call 可阻断、transform_context 可改写、project_trust 可裁决）——**拦截权只在进程内**，永不上线。

### 1.4 零 UI 原则的边界判例（什么算 UI，什么不算）

判据：**传输信息（事实/指令/问题）合法，决定样式（颜色/布局/组件/块词汇）越线**。

- ✅ 合法：事件（事实）、RPC 命令（指令）、反向原语 select/confirm/input（问题）、
  `has_ui`（有没有交互方——trust 决议等早期行为分支的输入）、capabilities（能力协商）；
- ❌ 越线：ui_blocks、themes、render 回调、键位表、mode（哪种前端——无消费者且曾被半接线，
  已删；pi 中其读者全是 UI widget 扩展拿它当 hasUI 用）；
- ⚖️ 中间态的正确归属：settings 的展示偏好字段（editor_padding_x 等）是**前端消费的数据**——
  schema 保留供 `getSettings`/`updateSettings` round-trip，运行时只存储不解释
  （typed accessor 已清，留下真实消费的 `block_images`/`image_auto_resize`）。
- ⚖️ 包管理是**兄弟服务**（不为 runtime 存活）：`core/package/` 的 install 世界
  （拉取/依赖/物化，盘上无状态重算）与 resolve 世界（已安装包 → 可加载资源）
  面向不同受众；`settings.json` 的 packages 列表是共享的**选择层**（安装写下选择、
  运行时读选择加载，"安装 ≠ 加载"）。它的三通道分工：**`nova-pkg` CLI = 开发者主通道**、
  **RPC package 域 = 应用内面板的可选通道**、**`pkgCheckUpdates` = 前端启动更新拉取**。

### 1.5 settings 的分层

- **运行时字段**：模型/重试/压缩/shell/包列表等——运行时真消费；
- **前端字段**：展示偏好——schema 保留、泛型通道读写、运行时零解释；
- **键位/主题**：不归 settings schema——前端自持文件（Node 层本机进程）。

### 1.6 扩展系统（Python 侧）

扩展 = **纯 agent 行为钩子**。可注册：事件 handler（Bus 3 拦截式）、命令（slash）、
flag（CLI 参数面）、shortcut handler（**执行体在运行时**）、provider、spawn hook。
不可做：UI 渲染（无通道）、工具注册（工具只走包管线，registerTool 明确不做）。

**shortcut 的正确分层**（判决记录）：handler 是运行时代码（拿 ExtensionContext），
所以注册表与执行在 Python；键位捕获/内置键位表/用户自定义/冲突裁决归前端。
过线两条：`getShortcuts`（目录）+ `invokeShortcut`（回调）。后端永远不知道
用户按的是哪个物理键——包括 ctrl+c/ctrl+d（raw 模式下只是字节，前端翻译为
`abort`/`shutdown`；后端生命周期由管道 EOF 与信号两条线管理）。

---

## 2. 线上协议（契约层，可插拔的根本）

### 2.1 四股流

| 流 | 方向 | 内容 |
|---|---|---|
| 事件 | 后 → 前（通知） | Bus 2 全量直通：`agent/event`，params = `{type, data}` 信封 |
| 命令 | 前 → 后（请求-响应） | 62 个方法（session/model/auth/resources/settings/system/user_tools/package 八域），全表见 `rpc_capabilities.md` |
| 快照 | 前 → 后（请求-响应） | `getSessionState` + `getSessionEntries`，连接/恢复全量重建 |
| 反向原语 | 后 → 前（请求-响应） | `ui/request` ↔ `ui/response` + `system/capabilities`（trust/OAuth/扩展询问） |

协议自查三问：是变更吗 → 命令；会让状态变吗 → 事件；新连上的前端要知道吗 → 快照。

### 2.2 帧与生命周期

- **帧**：NDJSON，一行一帧，即写即 flush；无 batch（行协议下无意义）；
- **并发**：每条入站消息独立 task（长 prompt 不阻塞 abort/steer）；阻塞型命令
  （包管理 pip 调用）经 `asyncio.to_thread`；写锁保证帧不撕裂；
- **关停**：前端退出 → stdin EOF → 服务器主循环退出清理；SIGTERM/SIGHUP →
  先杀跟踪中的 detached 子进程再关停；连接断开取消进行中的命令任务；
- **OutputGuard**：stdio 模式下杂散 stdout（依赖库的 print）重定向到 stderr，
  协议通道不可污染——stdio 传输的生死线；
- **流式**：全部连续数据走事件通道（token delta / 工具中间输出 / 进度通知），
  命令响应一次性——"命令一次性应答 + 一切连续变化事件化推送"。

### 2.3 类型管道（契约的机器化，已落地）

```
Python 类型（AgentSessionEvent 全集 + SessionEntry/SessionHeader）
   │  schema_export.py（annotation walker，复刻 serialize.py 落线语义）
   ▼
nova-wire.schema.json（语言中立快照）+ nova-wire.gen.ts（TS 判别联合）
   │  两工件入仓；--check + pytest 漂移测试保鲜
   ▼
nova-client：mapping/store/rpc-client 全部基于生成类型
```

- 单向不可逆：TS 只消费生成物；Python 改字段 → 重导 → TS 编译红；忘重导 → pytest 红；
- Literal 判别符一路继承（`type: Literal["agent_start"]` → `type: "agent_start"`），
  TS `switch` 自动窄化；
- 自由负载（工具 details 等 `Any` 字段）→ `unknown`，工具级类型归工具作者
  （将来复合包 `ui/` 段可附带 TS 类型）。

### 2.4 后端可插拔（多后端设计，定案）

**多后端 = 同一 Node 层可接入不同语言的 harness 实现**：nova-client 是常量，
harness 是实现变量（今天 Python，明天 Rust/Go）。契约只钉线上形状（方法词汇、
事件词汇、原语、生命周期语义）；**每个后端内部的生态形态（工具/扩展由什么语言
宿主、包管理长什么样）是其内部事务**——只要效果按契约上线即可。
唯一纪律：**别把实现语言的味道漏进协议**——方法域是可选项，靠能力位宣告
（如 Rust harness 初期可不注册 package 域，前端按位隐藏入口）。

接入只需满足同一契约：

| 条件 | 现状 |
|---|---|
| 传输：stdio NDJSON | ✅ 语言无关 |
| 事件契约 | ✅ `nova-wire.schema.json` 机器化（codegen/校验两用） |
| 命令形状（62 方法 params/result） | ✅ 已纳入 `schema_export`——形状在注册处声明（`methods/shapes.py`），分派前校验、schema `methods` 根、TS `NovaWireMethodMap` 三方同源 |
| 反向原语 + 能力协商 | ✅ 协议级 |
| `initialize` 能力位 | ✅ 真实化：`capabilities.domains/methods` 来自注册表（8 域 62 方法） |
| 契约版本对齐 | ✅ **major/minor 语义（R6 已修）**：`CONTRACT_VERSION_MAJOR/MINOR`（schema 工件 + `initialize` + TS 常量三处同源）——major 不等硬拒；minor 差放行，加法变更靠能力位与未知事件忽略降级 |
| 黑盒一致性套件 | ✅ v1（`tests/conformance/`：子进程 NDJSON 全双工 + schema 校验 + 反向原语往返 + 关停语义）；待补：LLM 事件覆盖（需假模型通道） |
| spawn 泛化 | ✅ rpc-client `command: string[]`（默认 Python harness，换任意语言二进制即可） |

---

## 3. nova-client（Node，TS）：呈现的一切

> 本节的完整施工图（定位/模块/机制/判决记录）见
> **`packages/nova-harness/frontend/docs/design.md`**；TUI 已作为内置宿主落位于
> `src/modes/tui/`（原独立 nova-tui 包已并入）。

### 3.1 六子系统，骨架已按 v3.1 一次成型

```
runtime.ts（facade，零业务，实现 RuntimeHost）
  ├─→ wire/（client：传输与线上分派；capabilities：major/minor 握手 + 能力位；
  │          bridge：反向原语路由）
  ├─→ bus/（观察式脊柱；mirror 特权订阅按序恰好一次）
  ├─→ session/（mirror：mapping 纯函数归约 + store 状态容器 + types 呈现词汇）
  ├─→ presentation/（blocks 声明式块 + slots 注册表）
  ├─→ packages/（pkgList 索引 + ui/ 资产发现 + npm 自愈）
  └─→ extensions/（渲染器加载器：jiti → slot 注册，trust 门控）
```

- **wire**：spawn/持有后端子进程（`command: string[]` 泛化，换语言无感知）；
  请求-响应按 id 配对 / `agent/event` → bus / 反向帧 → bridge；
  生命周期（shutdown 命令 → stdin EOF → exit → SIGKILL 兜底）；stderr 直通调试；
- **bus**：观察式 pub/sub，事件源架构，层间无直接调用；
- **mirror**：唯一被允许"读懂"事件形状的子系统。纯函数归约器
  `(state, event) → bool`（有无可视变更）——工具三条事件装配一张卡片、
  消息 N+2 条事件装配一条流式条目、spinner 状态机；累积替换（delta diff 归渲染层）；
  联合外事件类型静默忽略（向前兼容）；快照四事件（model_changed 等 payload 即事实）
  直写，其余不猜语义；

### 3.2 呈现模型（本层自有词汇，UI 无关）

`TranscriptEntry`（user/assistant/toolCall/notice/custom）、`ToolCallCard`
（running/done/error + partial + result）、`SessionStatus`（idle/working/compacting/retrying）、
`SessionSnapshot`。不是任何框架的组件——TUI/WebUI 拿同一份模型各自渲染。

### 3.3 观察式事件总线（定案：不是 pi 那种）

- pi 的总线是**拦截式**（handler 返结果阻断运行时）——因为 pi 扩展与 core 同进程；
- 我们的 UI 扩展**不可能拦截 agent**（另一个进程，且拦截已有家：Python Bus 3）；
- 所以 Node 层总线 = **观察式 pub/sub**：原始事件 tap（`events.on('tool_execution_end')`，
  通知类功能）+ store 派生便利事件（`session:synced`/`turn:started`）；
  handler 一律 `void`，无结果合并、无阻断语义（复杂度砍掉 80%）；
- 前端输入事件（按键/命令）**不进 bus**——键位归前端本地，命令经 RPC 直达后端。

### 3.4 渲染器注册表（M2，词汇已落地）

- `registerToolRenderer(toolName, fn)` + `registerMessageRenderer(role, fn)` + 通用回退；
- 输入即现有可渲染模型（`ToolCallCard.details` 平铺纯数据，数据面已备齐）；
- **组件模型：声明式块词汇优先**——渲染器返回声明式描述而非框架组件：
  v1 = `diff / markdown / code / json / table` 五种（**已落地**：`nova-client/src/presentation/blocks.ts` +
  `NovaRenderer` 契约）；每个前端实现一套块渲染器。
  （ui_blocks 构想在正确的层复活：Python 删它因为层错了，不是想法错了。）
- **逃生舱**：自由画布类扩展（space-invaders 级）可提供前端特定组件实现——
  默认声明式覆盖 80%，框架特定组件作为显式例外入口。
- **dogfood 已就位**：官方 bundle 的 `nova_coding_agent/ui/renderers/`
  （bash 终端风 / edit diff 风（消费引擎预生成 patch）/ read 文件风）——
  注册表与加载器（M2）开工即有真实消费对象。（曾有的 B 型独立渲染示范包
  `nova-coding-agent-ui/` 已随历史清理移除——B 型包形态保留，见 §3.5。）

### 3.5 UI 扩展宿主（M4，定案）

- **包模型：复合包 + 两种身份证**——A 型（pyproject 身份 + `ui/` 约定段，如官方
  bundle）；**B 型（包根 package.json 身份，纯 TS 包，无 pyproject——前后端作者
  解耦开发/发布的形态，已实现安装链路）**；`ui/` 不改 `[tool.nova]`，Python 不解释；
- **复合依赖编排已实现**：nova-pkg install 第 ④ 阶段后新增 **npm 阶段**——包根
  `package.json` 存在 → `npm ci --omit=dev`（有 lock）/ `npm install --omit=dev`；
  editable 源目录、copy 副本内执行；npm 缺失/离线/失败仅警告不阻断
  （Node 层加载时自愈）；
- **发现**：Node 层经 `pkgList` RPC 枚举已安装包 → 探测 `ui/` 段；
- **加载**：jiti（对齐 pi loader.ts，用户写 TS 免预编译）；
- **扩展 API** = 渲染器注册 + bus 订阅 + store 只读 + 声明式块 + 反向原语转交；
- **信任**：project trust 门控复用（不被信任项目的 ui/ 段不加载）；
- 一次安装、一个版本号、一个 trust 决策（避免 Jupyter 双体系教训）。

---

## 4. 前端（TUI / WebUI）

- **纯渲染 + 输入捕获**：拿着可渲染模型画像素，把手势翻译为命令；
- **键位全责**：内置键位表、用户自定义、冲突裁决、`double_escape_action` 类策略——
  全部前端本地状态；持久化自持文件或经 settings 泛型通道（schema 字段已备）；
- **块渲染器实现**：实现声明式块词汇（v1 五种），扩展渲染器经注册表接入；
- **WebUI 接入（M3）**：WebSocket 归 Node 层对外暴露（Python 永不开网络端口），
  多客户端 = 多前端连同一 Node 层，Node 扇出，Python 无感知。

---

## 5. 判决记录（本轮架构讨论的设计结论）

| 议题 | 判决 | 理由 |
|---|---|---|
| 键位与运行时 | 无关（键永不过线，过线的是命令） | 键是前端手势；扩展 shortcut 例外——执行体在运行时，故目录+回调两条线上线 |
| ExtensionMode | 删 | 读者全是 UI widget 扩展（拿它当 hasUI）；has_ui + capabilities 等价且更精确；原接线半坏 |
| has_ui | 留 | 早期行为分支输入（trust 问不问），构造期即知；capabilities 是迟到的精确信号 |
| 事件总线（Node） | 观察式 pub/sub | 拦截权不下放（agent 在另一进程；拦截归 Python Bus 3） |
| 组件模型 | 声明式块优先 + 逃生舱 | 不焊死框架（TUI/Web 共享）；ui_blocks 层错设想对 |
| UI 扩展包管理 | 复用 nova-pkg + `ui/` 约定段 | 一个产品一个包，一次安装一个 trust 决策 |
| 包身份证 | **双形态**：pyproject（A 型）/ 包根 package.json（B 型纯 TS 包） | 单注册表单安装器，但纯 TS 作者零 Python 接触（✅ 已实现） |
| 复合依赖编排 | **npm 阶段进 install**（包根 package.json → npm ci/install） | Python 不"管理"node 环境，npm 只是第四个被编排的子进程；Node 层自愈兜底（✅ 已实现） |
| 声明式块词汇 | diff/markdown/code/json/table 开放集 | 框架无关（✅ `presentation/blocks.ts` 已落地 + 官方 dogfood 渲染器） |
| TS 类型 | Python 构建期导出，零 npm codegen 依赖 | 类型真理在生产者；生成物非维护物 |
| JSON-RPC 实现 | 自实现（~750 行） | 双向流式/并发分派/OutputGuard/事件桥，PyPI 无覆盖；LSP 生态同判 |
| settings 展示字段 | schema 留、accessor 删 | 字段是前端数据（round-trip 需要），运行时 accessor 零消费 |
| migrate_settings | 整删 | pydantic 忽略未知键；第一版不需要 TS 时代迁移代码 |

---

## 6. 路线图

1. ~~**M2 前小修**~~（✅ R6 契约 major/minor + R7 abort 卡片收尾 + R8 快照双源合并
   + 四事件直写——与骨架重写合并一次成型）；
2. **M1 薄 TUI**（nova-tui 重写为 NovaUIRuntime + pi-tui 薄壳）：端到端第一个真实前端——**下一步**；
3. ~~**骨架升级（原 M2）**~~（✅ bus 脊柱化 + slot/presentation + 渲染器加载器
   已落地并对真实 dogfood 包跑通；settings/state 归并 M4；薄块适配器随 M1）；
4. **M3**：WebSocket 扇出（复用 bus 扇出语义）→ WebUI 开门；
5. **M4**：UI 扩展宿主（`ui/index.ts` 全量入口 + 扩展 API + settings/state +
   反向工具通道 + 区域逃生舱 + theme 加载 + 包面板内建扩展）；
6. **可插拔收口**：~~黑盒一致性套件~~（✅ v1 已落地；LLM 事件覆盖待假模型通道）——多后端就绪。

已完成：~~契约加固~~（方法形状/能力位/major·minor 契约版本/一致性套件 v1）~~npm 阶段~~
~~B 型包身份~~ ~~v1 块词汇 + 官方 dogfood 渲染器~~ ~~骨架六子系统（wire/bus/mirror/
presentation/packages/extensions）~~。
