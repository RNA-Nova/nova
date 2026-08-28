# Nova 架构 2.0 草案：三层模型

> 状态：**已定型（设计共识）**，第 1 步已完成。
> 本文档固化 2026-07 架构讨论的共识，是后续重构的图纸。

> **修订（2026-08，WS 扇出翻案）**：本文档"WebSocket 归 Node 层、Python 永不
> 直接暴露网络端口"的决议已**翻案**——经 codex app-server 的实证对照（单进程
> 多传输扇出是被生产验证的通解），RPC 层完成连接化重构（`rpc` 的
> `RpcServer` + `Connection` + `RoutingUIContext`），**WebSocket 传输归
> Python**（`transport/websocket.py`：bearer token 升级头鉴权 + 非 loopback
> 无显式 token 拒启 + Origin 白名单 403）；Node 层（TUI）与将来的浏览器同为
> 客户端，mirror/presentation 作为客户端侧 TS 库被两端复用。§2 的 stdio
> "唯一跨进程通道"与 §5.4 的传输分工以此修订为准。
> 与 `nova_ui_layer_redesign.md` 的关系：本文档是其"彻底版"——三层模型确立后，
> 旧稿中"Python 后端 + RPC 声明式 UI"的协议段**作废**，仅前后端分离的 RPC 骨架保留。
> 后续阅读：**`nova_architecture_2.1.md`**（落地终态 + Node 层/复合包/多后端完整设计）。

---

## 1. 设计宗旨（不可妥协的三条）

1. **框架零内置**：框架不内置任何工具、不预设任何工具名单；能力全部来自包市场。
2. **安装 ≠ 加载**：安装是静态注册（磁盘 + 清单），加载是运行时被选择触发的动态行为。
3. **选择有限**：选择了什么就加载什么、就激活什么；不选 = 无能力。

## 2. 三层架构

```
前端（TUI / Web UI）          纯渲染 + 用户输入
        ↕ 进程内（TUI）或 WebSocket（Web UI）
Node 扩展运行时                UI 的一切：UI 管线、扩展宿主（UI 扩展/theme/渲染组件）、
                               WebSocket 接入、多客户端事件扇出
        ↕ JSON-RPC over stdio（唯一跨进程通道）
Python agent 运行时（纯后端）   agent loop、工具执行、会话树持久化、compaction、
                               模型/auth、agent 行为钩子（事件/工具贡献/命令业务逻辑）
```

职责边界：

- **Python = 纯运行时**。不含任何 UI 概念（无 UIContext、无 ui_blocks、无 UI 原语）。
  Python 扩展 = **纯 agent 行为钩子**（session_start/compaction/project_trust/工具贡献）。
- **Node 层 = UI 的一切**。UI 扩展与渲染同进程（恢复 TS pi 的同构能力，无需跨进程注入代码）；
  theme 归此层（UI 资源，与 Python 无关）；对外提供 WebSocket（Web UI 接入）与进程内接口（TUI）。
- ~~**Python 永不直接暴露网络端口**~~（2026-08 翻案，见文首修订）：WebSocket
  传输归 Python `rpc`（连接化后 stdio/WS 同为连接来源，鉴权三守则：
  bearer token + 非 loopback 拒启 + Origin 白名单）。Python 的接入：
  `modes/print`（CLI 一次性）与 `modes/rpc`（stdio 长连接 / WS 多客户端）。

推论（修订后）：多客户端共享会话 = 多客户端连同一 **Python RPC 服务器**
（连接注册表广播扇出）；Node 层不再做 WS 中转扇出。

## 3. 运行时资源模型

### 3.1 四类能力资源（封闭集）

| 资源 | 内容 | 加载物 |
|---|---|---|
| `agents` | agent.yaml：能力组合定义（model + sections + 三白名单 + subagents） | 元数据（静态解析） |
| `tools` | executor.py + schema.json | 执行体（按需 import） |
| `skills` | SKILL.md / 裸 .md 模板（**prompts 已并入**） | 指令文本（按需读取） |
| `extensions` | 扩展模块（agent 行为钩子） | 模块（按需 import） |

处置项：

- **prompts**（prompt templates）并入 skills——同一物种的两种格式。
- **themes** 移出 Python 资源体系——归 Node 层 UI 资源（数据经 RPC/资产路径传输）。
- **context_files**（AGENTS.md 等）不是资源类，是 system prompt 的**项目约定输入源**。

### 3.2 索引全量（轻、静态） vs 代码加载按需（重、动态）

```
发现层（全量、静态、轻）
  扫描所有已安装包的 [tool.nova] manifest + schema.json + agent.yaml
  → 全局资源索引：资源名 → (包, 路径, 类型, 元数据, source 溯源)
  ※ 不 import 任何 executor / 扩展代码

选择层（agent_name）
  → 读 agent.yaml 白名单（tools/extensions/skills 名字清单）
  ※ 白名单必须显式列举，永不支持通配

加载层（按需、动态、重）
  → 按白名单查索引，只 import 命中路径的资源
  → 白名单引用未安装的名字 → 诊断（"X 未安装"，依赖显性化）
```

要点：

- **跨包共享天然兼容**：A 包的 agent 可引用 B 包的工具（共享发生在索引层，
  粒度精确到资源而非包）——"装了 B 只用它的 deploy"不连带加载 B 的一切。
- **source 线索**（SourceInfo）是索引层天然产物（路径即来源），
  支撑 collision 诊断、优先级裁决（project > user）、trust 门控、卸载生命周期。
- **SDK 显式工具**（custom_tools / base_tools_override）不走包体系、不进索引、
  始终可用（调用方代码里的显式意图）。
- **装了没选 = 无工具**：无 agent config 时 registry 不含包发现工具（仅 SDK 显式工具）。

## 4. 包模型：复合包

**逻辑上双管理，物理上统一**——避免"一个产品拆两个包"（Jupyter 双体系的教训）。

```
my-package/
├── nova-package.yaml（[tool.nova]）
│     agents / tools / extensions / skills 声明   ← Python 管（能力）
│     ui = ["./ui"]                              ← Node 管（UI 资产）
├── tools/…            （Python 能力）
└── ui/
    ├── themes/xxx.json    （主题）
    └── components/xxx.js  （渲染组件）
```

- **Python 包管理器（nova-pkg）**：统一负责安装/卸载/记录/更新——一次安装、
  一个版本号、一个 trust 决策。
- **Python ResourceLoader**：只管能力四类，`ui/` 目录仅做索引登记。
- **Node 层**：发现 `ui/` 资产并加载（读共享安装记录，或 Python 经 RPC 下发资产索引）。
- 纯 UI 包 = 只有 `ui` 段的复合包；纯能力包 = 无 `ui` 段的复合包。

## 5. RPC 协议（Python ↔ Node 的唯一通道）

### 5.1 与事件总线的关系

emit（总线）是**进程内分发**，RPC 是**跨进程搬运**。接触点只有一个：
**RPC 事件桥是 Bus 2（AgentSession 事件扇出）的一个普通订阅者**，
把事件序列化为 notification 发出。事件只出不进，命令只进不出。

### 5.2 四件套

| 类别 | 方向 | 命名 | 说明 |
|---|---|---|---|
| **命令** | 前 → 后 | 动词：`session/set_model`、`session/abort`、`tools/set_active` | 薄包装：解包参数 → 调真实方法 → 包装返回；**长命令发起即返回**（结果走事件） |
| **事件** | 后 → 前 | 过去式：`session/model_changed`、`agent/message_delta` | Bus 2 全量转发 |
| **快照** | 前 → 后（请求-响应） | `session/get_state` | 连接/恢复时全量快照（model/thinking_level/active_tools/queue/session_info/pending_count），之后增量事件维持 |
| **反向原语** | 后 → 前（请求-响应） | `ui/select`、`ui/confirm`、`ui/input` | 后端需要用户输入（trust 询问、OAuth、扩展 UI 请求） |

扩展命令走**通用转发**（`extension/call {name, args}` + `extension/notify`），
协议面稳定，不随扩展膨胀。

### 5.3 命令分派：并发 + 读写分流

查询类可排队；**控制类（abort/steer/interrupt）必须随时能插入**——
不得被长任务（turn 进行中的 prompt）阻塞。
（修复现状：RPC server 顺序处理导致 turn 期间 abort/steer 进不来。）

### 5.4 传输

- **stdio**：本地嵌入模式（TUI spawn 后端，共生共死）——当前主力。
- **WebSocket**：~~归 Node 层~~ → **归 Python `rpc`**（2026-08 翻案，
  见文首修订）；`WebSocketAcceptor` 每 accept 一条即 `add_connection`，
  与 stdio 共享同一方法表/事件流/反向原语。Node 层只持有 WS **客户端**。
- 协议内容（方法集/事件/快照/反向原语）与传输无关，传输层可替换。

### 5.5 协议自査三问（新场景验收标准）

1. 它是变更吗？ → 设计为命令（前→后）
2. 它会让状态变化吗？ → 必须有对应事件广播（后→前）
3. 新连上的前端能知道吗？ → 必须进快照

## 6. 迁移路径（三步走，每步独立有价值）

### 第 1 步：Python 纯运行时 + RPC 补全（当前阶段）

- 卸掉 UI 职责：UIContext 体系、ui_blocks（✅ 已全量清除：声明层 + `details["ui_blocks"]`
  数据通道，工具结果改平铺结构化 details）、RPC UI 原语（移交 Node 层设计）。
- RPC 补全：事件桥（Bus 2 → notification 全量转发——✅ 已落地为哑管道：`serialize.py`
  直通 `{type, data}` 信封，ui_events/event_mapper/content 三层映射与 listBlockTypes 已删）
  + 命令表（session/tools/model/auth/resources 域薄包装——✅ 已补齐）
  + 状态快照 + 并发分派（✅）。
- 并入**扩展系统审查**：双形态派发统一、ExtensionRunner 瘦身、错误形状定稿——
  Python 扩展收窄为纯 agent 行为钩子。
- 已知 bug 清零（agent.py:848、read 分页、OutputAccumulator UTF-8、RPC 顺序处理）。

### 第 2 步：Node 扩展运行时

nova-tui 演进为厚应用层：UI 管线 + 扩展宿主 + theme 加载 + WebSocket 接入 + 多客户端扇出。

### 第 3 步：复合包模型

`[tool.nova]` 增加 `ui` 资产段；nova-pkg 统一安装；ResourceLoader 收敛为能力四类 +
ui 索引登记；按需加载落地（索引层/加载层拆分）。

## 7. 已验证的既有成果（架构 2.0 的基座）

- tools 链路：框架零内置、零预设名单；默认激活 = registry 全部；激活三态
  （None 默认 / [] 显式空 / [names] 显式）；agent.yaml 白名单过滤。
- session/compaction/messages 与 TS 逐项对齐（见 `session_compaction_ts_alignment_audit.md`）。
- project_trust：emit 归扩展层、决策链安全语义。
- skills：加载+warning 诊断模型、发现规则、共享跳过名单。
- nova_ai：gateway/providers/auth/OAuth 与 TS 架构同构（无模块级状态）。
