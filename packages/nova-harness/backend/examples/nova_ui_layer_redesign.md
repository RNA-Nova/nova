# Nova UI 层重新设计：前后端分离的 pi 级 UI 粒度

> 状态：**已作废**——本文的"`details.ui_blocks` 声明式 block"路线被
> `nova_architecture_2.0.md` 三层模型取代（Python = 纯运行时，无任何 UI 概念；
> UI 资产与渲染归 Node 层，工具自定义 UI 走复合包 `ui/` 段）。
> 代码中的 `ui_blocks` 声明/数据通道已全量清除。保留本文仅作历史脉络参考。
> 本文取代 `nova_ui_architecture_design.md` 中的过渡方案。
> 目标：在不依赖任何具体前端的前提下，让后端 UI 层达到 pi（TS）**interactive 模式的全量粒度**，
> 且所有能力对"远程前端"（RPC/WebSocket 另一端）成立。

---

## 0. 设计基线（重要）

**不以 TS 的 RPC 模式为对标**。TS 的 RPC 子集是"次要用法降级"——它的 interactive 模式才是完整粒度。
而 Python 架构里远程前端**就是主 UI**：线协议必须承载 interactive 模式的全部能力，
否则 nova 的 TUI 永远是 pi 的降级版。

核心模型：**声明式视图 + 动作回传通道 + 状态同步**（LiveView / RN bridge 的远程 UI 模型）。
后端的"工厂函数"不可序列化，但工厂的产出（组件树、动作回调、数据流）全部可以协议化：

- 视图：组件类型 + props（schema 校验）
- 交互：前端组件经 `ui/action` 把按钮/按键/选择回传给后端注册者
- 数据：后端经 `ui/state` 推送编辑器文本、footer 数据、主题、状态

## 1. 现状问题（为什么要重写而不是修补）

1. **双 UIContext ABC**：`core/types/ui/context.py`（声明侧，含 widget/theme 等 11 个方法）与 `core/ui/context.py`（实现侧，含 component 三件套）无继承关系、表面不重合、能力词汇不同（`select` vs `select_list`）。
2. **协议半成品**：`modes/rpc/primitives.py` 死契约、`methods/ui.py` 占位不可达、`effects.py` 零引用、事件类型三个死类。
3. **事件不保真**：mapper 只覆盖 9 类；工具流式输出（`ToolExecutionUpdateEvent`）到不了前端；`message_delta` 只取第一个 block 且携带快照而非增量。
4. **语义含糊**："取消/超时/前端不支持"三种结果不可区分；notify 系列 fire-and-forget 不保序。

## 2. pi（TS）interactive 模式的全量粒度 → 远程化方案

| TS 能力 | 远程化方案 |
|---|---|
| select/confirm/input/editor（+signal/timeout 倒计时） | dialog 原语（timeout_ms 由前端显示倒计时） |
| notify、setStatus、setWorkingMessage/Visible/Indicator、setHiddenThinkingLabel、setTitle、setToolsExpanded | notify 原语 |
| setWidget(string[]/factory)、setFooter、setHeader | 声明式组件挂到命名 region；factory 的交互部分走 `ui/action` 回传；footer 数据经 `ui/state.footer_data` 推送 |
| **custom(factory, overlay)** | `custom(block_type, props, overlay_options)`：前端以 overlay+键盘焦点渲染注册组件，结果回传 |
| **renderCall/renderResult（工具渲染）** | 工具 `details.ui_blocks` 产出声明式 block（含 `shell: "self"` 提示），前端渲染器渲染；流式更新走 `tool_output` |
| **registerEntryRenderer** | custom entry 的 `custom_type` → 同名 block_type 渲染 |
| **addAutocompleteProvider** | `ui/request {method:"autocomplete", text, cursor}` → 后端扩展返回建议列表（请求/响应天然远程友好） |
| **setEditorComponent** | 编辑器归前端所有（前端插件机制），后端持有完整控制面：text get/set、paste 折叠、按键转发与改写、autocomplete 通道 |
| **onTerminalInput（consume+改写）** | `ui/action:terminal_input` 上行，handler 返回 consume/改写后的 data 下行 |
| theme 对象 / setTheme / getTheme / getAllThemes | theme 即数据（颜色 token），全量可序列化 |
| pasteToEditor（大文本折叠） | editor_text 原语 + collapse 提示参数 |
| FooterDataProvider（git 分支/状态/模型/token 统计） | `ui/state.footer_data` 推送 |

结论：**全量可远程化**，没有任何一项必须牺牲。

## 3. 分层设计

```
core/ui/
├── protocol.py      # 线协议 schema（hello/事件/请求/响应/动作/状态 + 版本握手）
├── events.py        # UIEvent 定义（保真）
├── mapper.py        # 内部事件 → UIEvent 的全量映射
├── context.py       # 唯一 UIContext ABC + DialogResult
├── noop.py          # 全 unsupported 空实现
├── transport_context.py  # 经 Transport 转发（保序、三态、timeout/signal）
├── blocks.py        # ContentBlock 核心类型（现 content.py 改名合并）
├── block_registry.py     # block 类型注册（schema 校验 + renderer 发现 + action 路由表）
└── (删除) effects.py / registry.py / components.py(并入 blocks)
types/ui/            # 删除整个目录（并入 core/ui）
modes/rpc/primitives.py   # 删除
protocol/methods/ui.py    # 删除（占位）
```

### 3.1 线协议（`protocol.py`）

**握手**（前端 → 后端，连接建立时）：

```json
{"method": "ui/hello", "params": {"protocol_version": 1, "streaming": "full",
  "batch_interval_ms": 50, "capabilities": [
  "select","confirm","input","editor","notify","status","working",
  "widget","title","editor_text","autocomplete","theme","terminal_input",
  "overlay","custom:bash_output","custom:diff"
]}}
```

- `streaming`：`"full" | "batched" | "final"`，缺省 `"full"`（见 3.2 节）
- `batch_interval_ms`：`batched` 档的合批间隔，缺省 50ms

**dialog / 查询请求**（后端 → 前端）：

```json
{"method": "ui/request", "params": {"id": "r1", "method": "select",
  "title": "Choose", "options": ["a","b"], "timeout_ms": 30000}}
{"method": "ui/request", "params": {"id": "r2", "method": "custom",
  "block_type": "login_form", "props": {...},
  "overlay": {"width": 60, "anchor": "center"}, "timeout_ms": 60000}}
{"method": "ui/request", "params": {"id": "r3", "method": "autocomplete",
  "text": "def ma", "cursor": 6}}
```

**响应**（前端 → 后端）：

```json
{"method": "ui/response", "params": {"id": "r1", "status": "ok", "value": "a"}}
// status: "ok" | "cancelled"；timeout 由后端计时判定
```

**通知**（后端 → 前端，fire-and-forget 但**保序**）：

```json
{"method": "ui/notify", "params": {"kind": "status", "key": "auth", "text": "..."}}
{"method": "ui/notify", "params": {"kind": "widget", "region": "aboveEditor",
  "key": "k", "block_type": "status_widget", "props": {...}}}
{"method": "ui/notify", "params": {"kind": "component_patch", "key": "k", "props": {...}}}
{"method": "ui/notify", "params": {"kind": "component_remove", "key": "k"}}
```

**前端动作/状态**（前端 → 后端）：

```json
{"method": "ui/action", "params": {"kind": "component_action", "key": "k",
  "name": "button_clicked", "payload": {...}}}
{"method": "ui/action", "params": {"kind": "terminal_input", "data": "..."}}
{"method": "ui/state", "params": {"editorText": "...", "toolsExpanded": true,
  "footer_data": {"git_branch": "main"}}}
```

### 3.2 事件层（`events.py` + `mapper.py`）

**全量保真**：`agent_session` 的每类事件都有线协议映射，丢失即 bug。骨架：

| 内部事件 | UIEvent | 说明 |
|---|---|---|
| MessageStart/End | `message_start/end` | 含 role、id |
| MessageUpdate | `message_delta` | **block 级增量**：`{message_id, content_index, block_type, delta}`，对齐 nova_ai 流式事件；多段 thinking/text 交错不丢 |
| ToolExecutionStart | `tool_call` | `{call_id, tool_name, args}` |
| ToolExecutionUpdate | `tool_output` | `{call_id, chunk 或 blocks, is_partial}`——bash 流式输出到前端 |
| ToolExecutionEnd | `tool_result` | `{call_id, blocks, is_error, elapsed_ms}` |
| AutoRetryStart/End | `status` | 携带 attempt、delay_ms、error message（不再压成 "Retrying..."） |
| AutoCompactionStart/End、CompactionStart/End | `status` | 携带 reason（manual/threshold/overflow） |
| TurnStart/End | `turn_start/end` | |
| QueueUpdate | `queue_update` | steering/follow-up 队列长度 |
| ModelSelect / ThinkingLevelChanged | `model_changed` / `thinking_level_changed` | |
| UserBash / ExtensionError | `user_bash` / `extension_error` | |
| session_*（switch/fork/compact/rename/entry_appended） | `session_event` | |

渲染数据（tool_result 的 content blocks）走 `ContentBlock` schema（现有 content.py 保留扩充）。

#### 流式分级（streaming levels）

事件流按前端能力/偏好分三档，握手时经 `ui/hello.streaming` 协商，**按连接生效**：

| 档位 | 行为 | 适用前端 |
|---|---|---|
| `"full"`（默认） | block 级 delta 逐事件推送（`message_delta` 带 `content_index`）；`tool_output` 逐段推送 | 富 TUI / Web UI，渐进渲染 |
| `"batched"` | 服务端按 `batch_interval_ms`（默认 50ms）合并连续 delta 后推送；消息数大幅减少，仍渐进 | 弱网环境、移动端、高频模型 |
| `"final"` | 不推 delta：assistant 消息只在结束时发 `message_final`（完整 content blocks）；工具只发 `tool_call` + `tool_result`（无 `tool_output` chunk） | 简单客户端、日志管道、批处理 |

实现要点：

- 在 mapper 与 transport 之间加一层 **`EventPipeline`**（per-connection）：`full` 透传、`batched` 按 interval 合批、`final` 抑制 delta 并合成终态
- `full` 档的 block 级 delta 直接转写自 `MessageUpdateEvent.assistant_message_event`
  （nova_ai 的 `text_delta`/`thinking_delta` 已带 `content_index`，原料现成；
  这也一并解决旧 mapper "携带快照、名不副实" 的问题）
- `final` 档的 `message_final` 从 `MessageEndEvent` 的完整消息快照合成
- `batched` 合批键：`message_id + content_index + block_type`；`tool_output` 按 `call_id + stream` 合批
- print 模式天然走 `final`；NoOpUIContext 不关心分级

### 3.3 UIContext（唯一 ABC）

```python
class UIContext(ABC):
    capabilities: Set[str]                     # 前端 hello 上报

    # dialogs（返回 DialogResult，三态可区分）
    async def select(title, options, *, timeout_ms=None, signal=None) -> DialogResult[str]
    async def confirm(title, message, *, timeout_ms=None, signal=None) -> DialogResult[bool]
    async def input(title, placeholder=None, *, timeout_ms=None, signal=None) -> DialogResult[str]
    async def editor(title, prefill=None, *, timeout_ms=None, signal=None) -> DialogResult[str]
    async def custom(block_type, props, *, overlay=None, timeout_ms=None, signal=None) -> DialogResult[Any]

    # notify（保序）
    def notify_message(message, level="info")
    def set_status(key, text=None)
    def set_working(message=None, visible=None, frames=None, interval_ms=None)
    def set_hidden_thinking_label(label=None)

    # surfaces（声明式组件，region 挂载 + patch/remove 生命周期）
    def set_widget(key, block_type, props, *, region="aboveEditor")
    def set_footer(block_type, props=None)     # None 恢复默认
    def set_header(block_type, props=None)
    def patch_component(key, props)
    def remove_component(key)
    def set_title(title)
    def paste_to_editor(text)
    def set_editor_text(text)
    def set_tools_expanded(expanded)
    def set_theme(name) -> DialogResult[bool]

    # 输入、动作回传与状态
    def on_terminal_input(handler) -> unsubscribe          # handler 可 consume 或改写 data
    def on_component_action(key, handler) -> unsubscribe   # 组件动作回传（交互式组件）
    def get_editor_text() -> str                           # 来自前端 ui/state 同步
    def get_tools_expanded() -> bool
    def get_theme(name) / get_all_themes()
```

```python
@dataclass
class DialogResult(Generic[T]):
    status: Literal["ok", "cancelled", "timeout", "unsupported"]
    value: Optional[T] = None
```

### 3.4 声明式组件与渲染器（工厂能力的远程等价物）

复用并升级现有 `ui_blocks` 包资源：

```
ui_blocks/bash_output/
    schema.py        # props 契约（NovaBaseModel，含 type 字面量）
    renderer.json    # {"component": "BashOutput", "path": "./renderer.tsx"}
    renderer.tsx     # 前端渲染器（含交互逻辑：按钮/折叠/滚动）
```

- `BlockTypeRegistry` 注册 `block_type → (schema, renderer_component, renderer_path, source_info)`；前端经 RPC `listBlockTypes` 拉取全部渲染器
- **工具渲染**：工具在 `details.ui_blocks` 返回 block dict（可带 `shell: "self"`），前端按 `type` 找渲染器 → 等价 TS `renderResult`
- **条目渲染**：扩展 `append_entry(custom_type=...)` 映射为同名 block_type → 等价 TS `registerEntryRenderer`
- **交互式组件**：`custom()` / `set_widget()` 挂上的组件可发 `component_action`，经 `on_component_action(key, handler)` 路由回注册者（扩展）→ 等价 TS factory 内的键盘/按钮回调
- **autocomplete**：扩展注册 provider → 前端随输入发 `autocomplete` 请求 → 扩展返回建议 → 等价 TS `addAutocompleteProvider`

### 3.5 能力分级

| 级别 | 能力 | 说明 |
|---|---|---|
| L0 | notify、status、select/confirm/input/editor | 任何前端必须实现，否则 dialog 全 unsupported |
| L1 | custom 组件、widget/footer/header、theme、terminal_input、editor_text、autocomplete | 富前端（完整粒度） |
| L2 | overlay 布局策略、前端 chrome、编辑器实现 | 前端私域，不抽象 |

降级语义：`unsupported` 显式返回——与 `cancelled`（用户主动取消）、`timeout`（后端计时超时）严格区分。

### 3.6 TransportUIContext 的行为修正

- **保序**：所有写操作进单条写队列（不再 `asyncio.create_task` 乱序 fire-and-forget）；notify 无响应也排队
- **三态**：能力缺失 → `unsupported`；超时 → `timeout`；前端 `cancelled` → `cancelled`
- **timeout/signal**：每个 dialog 支持独立 `timeout_ms` 与 `AbortSignal`（对齐 TS `ExtensionUIDialogOptions`）
- **terminal_input**：handler 返回 `{consume, data}`，改写后的 data 经下行消息回注前端
- **component_action**：按 key 路由到 `on_component_action` 注册的 handler
- **state 同步**：`ui/state` 更新本地缓存，同步 getter 读缓存（文档注明可能滞后一帧）

## 4. 删除清单

- `core/types/ui/`（整个目录，双 ABC 的另一套）
- `core/ui/effects.py`、`core/ui/registry.py`（无人用）、`core/ui/components.py`（并入 blocks）
- `modes/rpc/primitives.py`、`core/protocol/methods/ui.py`
- `core/ui/events.py` 死类型（ToolOutputEvent 会被新设计真正用起来，SessionEvent/ErrorEvent 按新事件表重写）
- `types/runtime/tools.py` 的 `render_call/render_result/prepare_arguments` 死字段

## 5. 与 TS interactive 模式的全量对照

| TS | 新设计 | 状态 |
|---|---|---|
| select/confirm/input（+signal/timeout） | 同名 + DialogResult 三态 | ✅ |
| editor | 同名 | ✅ |
| notify | notify_message | ✅ |
| onTerminalInput（consume+改写） | 同名（改写经下行回注） | ✅ |
| setStatus/setWorkingMessage/Visible/Indicator | set_working 一族 | ✅ |
| setHiddenThinkingLabel | 同名 | ✅ |
| setWidget(string[]) | set_widget 数据形态 | ✅ |
| setWidget(factory)/setFooter/setHeader | 声明式组件 + `component_action` 回传 | ✅ |
| setTitle | 同名 | ✅ |
| custom(factory, overlay) | custom(block_type, props, overlay) | ✅ |
| renderCall/renderResult | ui_blocks + `tool_output` 流式 | ✅ |
| registerEntryRenderer | custom_type → block_type | ✅ |
| addAutocompleteProvider | autocomplete 请求/响应 | ✅ |
| pasteToEditor/setEditorText/getEditorText | 同名 | ✅ |
| setToolsExpanded/getToolsExpanded | 同名 | ✅ |
| setTheme/getTheme/getAllThemes | theme 数据化 | ✅ |
| setEditorComponent/getEditorComponent | 编辑器归前端 + 后端全控制面 | ✅ |
| FooterDataProvider | `ui/state.footer_data` 推送 | ✅ |

## 6. 实施顺序

1. `protocol.py` + `events.py` 新 schema（纯类型，无行为变化）
2. `context.py` 唯一 ABC + `noop.py` + `transport_context.py` 保序三态
3. `mapper.py` 全量化 + `EventPipeline`（streaming 三档）
4. 删除清单清理 + 全部引用点迁移（agent/factory/sdk/runner/session config/project_trust → `core.ui`）
5. `block_registry` renderer 发现 + `listBlockTypes` RPC + `component_action` 路由
6. 测试：协议 schema、三态、保序、全量事件映射、streaming 三档、声明式组件流、action 回传
