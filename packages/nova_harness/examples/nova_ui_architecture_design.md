# Nova UI 架构设计稿

> 状态：**已作废**——本文的"Python 后端 + RPC 声明式 UI（`ui_blocks`）"路线被
> `nova_architecture_2.0.md` 三层模型取代（Python = 纯运行时，无任何 UI 概念；
> UI 资产与渲染归 Node 层）。代码中的 `ui_blocks` 声明/数据通道已全量清除。
> 保留本文仅作历史脉络参考。  
> 范围：`nova_harness` 前后端分离场景下的 UI 管线  
> 目标：彻底理清当前两套 UI 系统的混乱，设计一套统一、可流式、可降级、可扩展的 UI 架构

---

## 一、当前代码诊断

### 1.1 两套并行的 UI 抽象

当前 `nova_harness` 里存在**两套没有整合的 UI 体系**：

| 体系 | 位置 | 用途 | 问题 |
|---|---|---|---|
| **Content Stream** | `core/ui/events.py` + `core/ui/mapper.py` | 把 AgentSession 内部事件映射为前端可渲染的线协议事件 | 只覆盖了消息和部分工具事件；工具中间输出、会话生命周期等未映射 |
| **Interactive UI** | `core/ui/context.py` + `core/ui/transport_context.py` | 扩展/后端向前端请求对话框、通知、组件 | 与 Content Stream 共享 transport 但消息形状完全不同；`core/types/ui/context.py` 还有一份旧实现 |
| **Effect/Registry** | `core/ui/effects.py` + `core/ui/registry.py` | 扩展返回 UI Effect、后端维护组件注册表 | **完全没有被使用** |
| **Block Registry** | `core/ui/block_registry.py` | 动态注册 ContentBlock 类型 | 已接入，但和 Content Stream 的联动不完整 |

核心问题：

1. **一个 Transport 跑两套协议**。`agent/event` 推送内容事件，`ui/request`、`ui/notify`、`ui/component/*` 处理交互。前端必须同时理解两套语义。
2. **两个 `UIContext`**。`core/ui/context.py` 有组件能力，`core/types/ui/context.py` 没有。`AgentSession` 引用的是旧版，运行时却注入新版，类型不一致。
3. **Content Stream 不完整**。`ToolExecutionUpdateEvent` 已产生但 `mapper.py` 不处理；`SessionStartEvent` / `SessionInfoChangedEvent` 不映射到 UI。
4. **交互式 UI 和 Content Stream 不互通**。工具返回的 `ui_blocks` 可以渲染结构化输出，但扩展无法通过 Content Stream 直接插入自定义组件。
5. **缺少流式策略控制**。要么全流式，要么无流式，不能按前端能力、按工具、按请求动态调整。

### 1.2 当前事件流

```
AgentSession._emit(event)
  → 订阅者（NovaServer._subscribe_session）
    → map_event(event) → UIEvent
      → NovaServer.send_event("agent/event", UIEvent)
        → Transport.write(JSON-RPC notification)
```

这个链路本身是合理的，但 `map_event` 是一张不完整的表。

### 1.3 当前交互 UI 流

```
Extension / AgentSession
  → UIContext.request("select", {...})
    → TransportUIContext
      → Transport.write({method: "ui/request", params: {id, component: {...}}})
        → 前端回复 ui/response
          → UIContext.resolve_response → UIResponse
```

这个链路也合理，但只用于模态交互，和内容渲染无关。

---

## 二、设计目标

1. **统一管线**：同一条 transport 上只跑一种 JSON-RPC 语义，但允许两种 envelope（`agent/event` 和 `ui/*`）。
2. **全流式**：消息、工具输出、状态、会话生命周期、错误都能增量推送。
3. **可选非流式**：简单客户端可以选择只接收最终结果。
4. **工具即渲染器**：工具通过 `schema.json` 声明 `ui_blocks`，执行中通过 `on_update` 推送中间块。
5. **扩展可插入 UI**：扩展通过 Content Stream 返回 `ui_blocks`，或通过 Interactive UI 请求模态组件。
6. **清理重复**：合并两个 `UIContext`，删除/激活未使用的 `effects.py` / `registry.py`。

---

## 三、统一 UI 线协议

### 3.1 两类消息

| 消息 | 方向 | 用途 |
|---|---|---|
| `agent/event` | 后端 → 前端 | 会话内容、状态、生命周期增量 |
| `ui/*` | 双向 | 交互式组件请求/通知/反向通道 |

所有 `agent/event` 的 `params` 都是一个 **UIEvent**。

### 3.2 UIEvent 统一 schema

```ts
type UIEvent =
  // 消息流
  | { type: "message_start"; message_id: string; role: "user" | "assistant" }
  | { type: "message_delta"; message_id: string; delta: ContentBlock }
  | { type: "message_end"; message_id: string }

  // 工具流
  | { type: "tool_call"; call_id: string; tool_name: string; display: ContentBlock }
  | {
      type: "tool_output";
      call_id: string;
      tool_name: string;
      chunk: string;                       // 文本增量（如 bash stdout）
      stream: "stdout" | "stderr" | "status" | "output";
      blocks: ContentBlock[];              // 结构化预览块（可选）
      metadata: Record<string, any>;       // truncation、duration_ms 等
      is_partial: boolean;
    }
  | { type: "tool_result"; call_id: string; tool_name: string; content: ContentBlock[]; is_error: boolean; elapsed_ms?: number }

  // 会话生命周期
  | { type: "session"; kind: "created" | "switched" | "forked" | "cloned" | "imported" | "compact" | "renamed"; session_id: string; session_name?: string; data?: any }

  // 状态
  | { type: "status"; kind: "idle" | "working" | "compacting" | "error"; message?: string }

  // 错误
  | { type: "error"; message: string; details?: any }
```

`ContentBlock` 保持为开放字典，由 `BlockTypeRegistry` 动态发现 schema。

### 3.3 交互 UI 消息

保持现有 `ui/request`、`ui/notify`、`ui/response`、`ui/state`、`ui/event`、`ui/component/set|patch|remove`。但统一把 `component` 概念也纳入 Content Stream：扩展返回的 `ui_blocks` 可以直接是 `component` 类型块。

---

## 四、StreamingPolicy：可选流式控制

### 4.1 三层开关

```python
@dataclass
class StreamingPolicy:
    messages: bool = True          # message_delta
    tool_output: bool = True       # tool_output 中间事件
    tool_calls: bool = True        # tool_call 即时出现
    status: bool = True            # status 事件
    session_events: bool = True    # session 生命周期事件
    batched: bool = False          # true 时后端把事件数组打包发送
    batch_window_ms: int = 0       # batched=true 时的最大攒批窗口
```

### 4.2 配置来源（优先级从低到高）

1. **模式默认值**：`print` 模式全关；`rpc`/`websocket`/`tui` 模式全开。
2. **会话创建参数**：`createSession({ streaming: {...} })`。
3. **单次请求参数**：`prompt({ streaming: {...} })` 覆盖。
4. **工具声明**：`schema.json` 中 `streaming: false` 可关闭该工具的 `tool_output`。

### 4.3 非流式行为

当某一项关闭时：

- `messages=false`：不发 `message_delta`，整段生成完后在 `message_end` 里附带完整 `content`（或让 `prompt` 响应返回）。
- `tool_output=false`：不发 `tool_output`，只发最终 `tool_result`。
- `status=false`：不发中间 `status(working)`。
- `session_events=false`：会话生命周期事件仍建议发送，因为属于状态同步而非内容流。

内部执行逻辑不变，只在 `NovaServer` 出口处按策略过滤/缓冲。

---

## 五、工具流式输出契约

### 5.1 工具声明

```json
{
  "name": "bash",
  "description": "执行 shell 命令",
  "parameters": { ... },
  "ui_blocks": ["bash_output"],
  "streaming": true,
  "streams": ["stdout", "stderr"]
}
```

- `streaming`：是否允许产生 `tool_output` 事件。
- `streams`：可选，声明工具会输出哪些流。

### 5.2 执行期流式推送

工具 `execute(tool_call_id, params, signal, on_update)` 的 `on_update` 接收 `AgentToolResult`：

```python
on_update(AgentToolResult(
    content=[TextContent(type="text", text="...")],
    details={
        "ui_blocks": [{"type": "bash_output", ...}],  # 结构化预览
        "truncation": {...},
        "full_output_path": "/tmp/...",
    }
))
```

后端映射规则：

- `content[].text` → `tool_output.chunk`
- `details.ui_blocks` → `tool_output.blocks`
- `details` 中除 `ui_blocks` 外的字段 → `tool_output.metadata`
- `stream` 字段由工具执行器决定：bash 分别推送 stdout/stderr，其他工具默认 `output`

### 5.3 Bash 工具改造

`OutputAccumulator` 从单缓冲改为三缓冲：

```python
@dataclass
class OutputSnapshot:
    stdout: str
    stderr: str
    combined: str
    truncation: TruncationInfo
    full_output_path: Optional[str]
```

`on_update` 分别推送：

- `stream="stdout"` + `chunk=stdout_delta`
- `stream="stderr"` + `chunk=stderr_delta`
- `stream="output"` + `blocks=[bash_output_block]`（结构化预览，可选）

最终 `tool_result` 返回完整 `bash_output` 块。

---

## 六、扩展 UI 集成

### 6.1 扩展通过 Content Stream 返回 UI

扩展的 `execute` 返回 `AgentToolResult` 时，可以在 `details.ui_blocks` 中放入 `component` 类型块：

```json
{
  "type": "component",
  "component_type": "my_chart",
  "key": "chart-1",
  "props": {"data": [...]}
}
```

前端通过 `BlockTypeRegistry` 发现 `component` block 的 schema，并渲染对应组件。

### 6.2 扩展通过 Interactive UI 请求模态

保留现有 `UIContext.request/select/confirm/editor/custom`。扩展通过 `ExtensionContext.ui` 调用。

### 6.3 UI Effect 系统激活

当前 `core/ui/effects.py` 未被使用。建议：

- 扩展不直接调用 `UIContext`，而是返回 `UIEffect`。
- `ExtensionRunner` 或 `AgentSession` 统一把 Effect 应用到 `UIContext`。
- 这样后端可以做能力检查、审计、生命周期管理。

```python
UIEffect = UIRequestEffect | UINotifyEffect | UIComponentEffect
```

---

## 七、内部事件 → UIEvent 完整映射表

| 内部事件 | UIEvent | 条件/备注 |
|---|---|---|
| `MessageStartEvent` | `message_start` |  |
| `MessageUpdateEvent` | `message_delta` | 只映射 text/thinking 块 |
| `MessageEndEvent` | `message_end` |  |
| `ToolExecutionStartEvent` | `tool_call` |  |
| `ToolCallEvent` | `tool_call` |  |
| `ToolExecutionUpdateEvent` | `tool_output` | **新增**；受 `StreamingPolicy.tool_output` 和工具 `streaming` 声明控制 |
| `ToolExecutionEndEvent` | `tool_result` |  |
| `ToolResultEvent` | `tool_result` |  |
| `CompactionStartEvent` / `AutoCompactionStartEvent` | `status(compacting)` |  |
| `CompactionEndEvent` / `AutoCompactionEndEvent` | `status(idle)` |  |
| `AutoRetryStartEvent` | `status(working, "Retrying...")` |  |
| `AutoRetryEndEvent` | `status(idle)` |  |
| `SessionStartEvent` | `session(created/switched/forked/cloned/imported)` | **新增** |
| `SessionInfoChangedEvent` | `session(renamed)` | **新增** |
| `SessionCompactEvent` | `session(compact)` | **新增** |
| `ExtensionErrorEvent` | `error` | **新增** |

---

## 八、架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (TUI / Web / IDE)              │
│  ┌──────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ Event Renderer│  │ Interactive UI  │  │ BlockTypeRegistry   │ │
│  └──────┬───────┘  └────────┬────────┘  └─────────────────────┘ │
└─────────┼──────────────────┼───────────────────────────────────┘
          │                  │
          │ JSON-RPC over Transport (stdio / WebSocket / memory)
          │                  │
┌─────────┼──────────────────┼───────────────────────────────────┐
│         ▼                  ▼                                   │
│  ┌──────────────┐  ┌─────────────────┐                         │
│  │ NovaServer   │  │ UIContext       │                         │
│  │              │  │ (TransportUI/   │                         │
│  │ - agent/event│  │  NoOpUI)        │                         │
│  │ - ui/*       │  │                 │                         │
│  └──────┬───────┘  └─────────────────┘                         │
│         │                                                        │
│  ┌──────▼───────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ UIEvent      │  │ StreamingPolicy │  │ BlockTypeRegistry   │ │
│  │ mapper       │  │                 │  │                     │ │
│  └──────┬───────┘  └─────────────────┘  └─────────────────────┘ │
│         │                                                        │
│  ┌──────▼───────┐                                                │
│  │ AgentSession │── events                                       │
│  └──────────────┘                                                │
│         │                                                        │
│  ┌──────▼───────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ Agent (nova) │  │ Tools       │  │ ExtensionRunner         │ │
│  └──────────────┘  └─────────────┘  └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 九、文件改造清单

### 9.1 删除/合并

- `core/types/ui/context.py`：合并到 `core/ui/context.py`，只保留一份 `UIContext`。
- `core/types/ui/__init__.py`：重新导出 `core/ui/context.py` 中的 `UIContext`，保持兼容。

### 9.2 修改

- `core/ui/events.py`：扩展 `ToolOutputEvent`，新增 `tool_name` / `blocks` / `metadata` / `is_partial`。
- `core/ui/mapper.py`：补充 `ToolExecutionUpdateEvent`、`SessionStartEvent`、`SessionInfoChangedEvent`、`SessionCompactEvent`、`ExtensionErrorEvent` 映射。
- `core/server.py`：引入 `StreamingPolicy`，在 `send_event` 前按策略过滤/缓冲。
- `core/agent_session/agent.py`：确保 `new_session` / `switch_session` / `fork_session` / `clone_session` / `import_session` / `set_session_name` 都触发对应事件（已大部分实现，只需 mapper 对接）。
- `core/types/runtime/tools.py`：`ToolDefinition` 增加 `streaming` / `streams` 字段。
- `core/resources/loaders/tools.py`：从 `schema.json` 读取 `streaming` / `streams`。
- `core/harness/tools/dynamic_tool.py`：暴露 `streaming` / `streams` / `ui_blocks`。
- `nova_coding_agent/tools/bash.py`：改造 `OutputAccumulator` 为双缓冲，分别推送 stdout/stderr/tool_output。
- `nova_coding_agent/tools/bash/schema.json`：声明 `streaming: true, streams: ["stdout", "stderr"]`。

### 9.3 可选增强

- `core/ui/effects.py`：接入 `ExtensionRunner`，让扩展返回 Effect 而非直接调用 UI。
- `core/ui/registry.py`：维护持久组件，前端重连时同步。
- `core/ui/block_registry.py`：增加 `component` 作为内置 block 类型。

---

## 十、前端消费示例

```ts
// 建立会话并声明能力
await rpc.request("createSession", {
  model: "volcengine/doubao-pro",
  streaming: { messages: true, tool_output: true, batched: false }
});

// 监听所有事件
rpc.onNotification("agent/event", (event) => {
  switch (event.type) {
    case "message_delta":
      appendText(event.message_id, event.delta.text);
      break;
    case "tool_call":
      showToolCard(event.call_id, event.display);
      break;
    case "tool_output":
      appendToolOutput(event.call_id, event.stream, event.chunk, event.blocks);
      break;
    case "tool_result":
      finalizeTool(event.call_id, event.content, event.is_error);
      break;
    case "session":
      updateSessionTree(event);
      break;
  }
});

await rpc.request("prompt", { text: "运行 npm test" });
```

---

## 十一、与 TypeScript 版本的差异

TS 版本把渲染逻辑内嵌在工具里（`renderCall` / `renderResult`），直接把 TUI 组件返回给前端。Python 版本走前后端分离路线，所以：

- TS：工具返回组件对象 → TUI 直接渲染。
- Python：工具返回 `ui_blocks` / `component` 数据块 → 前端根据 schema 渲染。

Python 这条路更适合 Web/IDE 插件，但需要前端具备组件注册能力。`BlockTypeRegistry` + `listBlockTypes` RPC 已经为这个方向打下基础。

---

## 十二、总结

当前 Python 版本的 UI 管线**骨架已经有了**，但处于“半成品”状态：

- ✅ Transport 抽象合理
- ✅ JSON-RPC 协议合理
- ✅ Content Stream 事件类型合理
- ✅ BlockTypeRegistry 已接入
- ❌ 工具中间输出未映射
- ❌ 会话生命周期未映射
- ❌ 两套 UIContext 未合并
- ❌ UI Effect/Registry 未使用
- ❌ 缺少流式策略控制

按本设计稿改造后，Nova Python 版将具备：

1. 完整的内容流式推送（消息 + 工具 + 状态 + 会话）
2. 可选的非流式/批量模式
3. 清晰的交互式 UI 与内容渲染分离
4. 工具自声明渲染能力
5. 扩展可插入自定义 UI 组件
