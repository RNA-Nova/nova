# Nova (Python) vs π (TypeScript) 事件总线对比

> 范围：两个项目的 Agent / AgentSession / Extension / 扩展间通信四层总线架构
> 目的：确认 π 的设计，为 Nova 的 mapper / UI 协议 / 扩展总线改造提供参考
> 更新日期：2026-07-15

---

## 一、总体结论

**π 和 Nova 的事件总线架构几乎一致，都是 4 层并行总线**，而不是之前粗略对比中说的 3 层。两者都存在同样的事件分流问题，也都把 `model_select` / `session_start` 这类"主动操作结果"放在 ExtensionRunner 层，而不是 AgentSession 公开事件流。

| 维度 | Nova (Python) | π (TypeScript) |
|---|---|---|
| 总线层数 | **4 层** | **4 层** |
| 事件结构 | dataclass + Pydantic (`NovaBaseModel`) | TypeScript discriminated union |
| AgentEvent 范围 | 10 个事件 | 10 个事件 |
| AgentSessionEvent 范围 | AgentEvent + 8 个会话级事件 | AgentEvent(改写 `agent_end`) + 8 个会话级事件 |
| ExtensionEvent 范围 | 28+ 个事件（含工具细分） | 28+ 个事件（ToolCall/ToolResult 按工具名细分） |
| 扩展间总线 | `ExtensionEventBus` | `EventBus` (`node:events` 包装) |
| RPC 输出 | 内部事件 → `UIEvent` mapper → JSON-RPC `agent/event` | 直接序列化 `AgentSessionEvent` → JSONL |
| 前端耦合 | 试图解耦（`UIEvent` 稳定契约） | 直接消费 AgentSessionEvent |
| 当前主要问题 | `tool_execution_update` 未映射到 `tool_output`；`UIEvent` 层不完整 | 没有独立 UIEvent 层；RPC 不支持 TUI 专属 UI 原语 |

**最核心差异**：Nova 在 AgentSession 公开事件与 RPC/前端之间插入了一层 `UIEvent` + `mapper`，意图做前后端解耦；π 没有这个中间层，TUI 直接消费 `AgentSessionEvent`，RPC 直接输出 `AgentSessionEvent`。这导致 Nova 的 mapper 必须手动维护，容易遗漏事件；π 则不存在"事件丢失"问题，但前端协议与内部事件深度耦合。

---

## 二、四层总线架构

### Bus 1：底层 `Agent` 总线

| 项目 | 位置 | 核心实现 |
|---|---|---|
| Nova | `packages/nova_agent/src/nova_agent/agent.py:566` | `async def _emit(self, event: AgentEvent)` |
| π | `packages/agent/src/agent.ts:400+` | `private processEvents(event)` → `for (const listener of this.listeners)` |

**事件列表（两者完全一致）**：

- `agent_start`
- `agent_end`
- `turn_start`
- `turn_end`
- `message_start`
- `message_update`（仅 assistant 消息流式过程）
- `message_end`
- `tool_execution_start`
- `tool_execution_update`
- `tool_execution_end`

**分发语义**：
- Nova：`AgentListener` 支持同步或异步，使用 `asyncio.create_task(asyncio.wait_for(listener, 120))`，带 120 秒超时。
- π：listener 签名 `(event: AgentEvent, signal: AbortSignal) => Promise<void> | void`，按订阅顺序 `await`。

**重要区别**：
- Nova 与 π 在 Agent 层的 `agent_end` 都只包含 `messages`。
- 两者在 AgentSession 层都会改写 `agent_end`：π 为 `{ type: "agent_end", messages, willRetry }`，Nova 为 `AgentEndEvent(messages=..., will_retry=...)`。

---

### Bus 2：`AgentSession` 公开事件总线

| 项目 | 位置 | 核心实现 |
|---|---|---|
| Nova | `packages/nova_harness/src/nova_harness/core/agent_session/agent.py:596` | `def _emit(self, event: Any)` |
| π | `packages/coding-agent/src/core/agent-session.ts:458` | `private _emit(event: AgentSessionEvent)` |

**Nova 的 AgentSessionEvent 定义**：

```python
# packages/nova_harness/src/nova_harness/core/types/events/unions.py:73
AgentSessionEvent = Union[
    AgentEvent,
    AutoCompactionStartEvent,
    AutoCompactionEndEvent,
    AutoRetryStartEvent,
    AutoRetryEndEvent,
    QueueUpdateEvent,
    SessionInfoChangedEvent,
    ThinkingLevelChangedEvent,
    CompactionStartEvent,
    CompactionEndEvent,
]
```

**π 的 AgentSessionEvent 定义**：

```typescript
// packages/coding-agent/src/core/agent-session.ts:124
export type AgentSessionEvent =
  | Exclude<AgentEvent, { type: "agent_end" }>
  | { type: "agent_end"; messages: AgentMessage[]; willRetry: boolean }
  | { type: "queue_update"; steering: readonly string[]; followUp: readonly string[] }
  | { type: "compaction_start"; reason: "manual" | "threshold" | "overflow" }
  | { type: "session_info_changed"; name: string | undefined }
  | { type: "thinking_level_changed"; level: ThinkingLevel }
  | { type: "compaction_end"; reason: "manual" | "threshold" | "overflow"; result: CompactionResult | undefined; aborted: boolean; willRetry: boolean; errorMessage?: string }
  | { type: "auto_retry_start"; attempt: number; maxAttempts: number; delayMs: number; errorMessage: string }
  | { type: "auto_retry_end"; success: boolean; attempt: number; finalError?: string };
```

**两者差异**：

| 事件 | Nova | π | 说明 |
|---|---|---|---|
| `agent_end` | 改写为 `{ messages, will_retry }` | 改写为 `{ messages, willRetry }` | 两者都会在 AgentSession 层补充 willRetry/will_retry |
| `queue_update` | ✅ | ✅ | 都包含 steering / follow-up 队列 |
| `compaction_start` | ✅ | ✅ | 都包含 reason |
| `compaction_end` | ✅ | ✅ | π 包含 result/aborted/willRetry/errorMessage；Nova 的 `CompactionEndEvent` 也有这些字段 |
| `auto_retry_start` | ✅ | ✅ | 都包含 attempt/maxAttempts/delayMs/errorMessage |
| `auto_retry_end` | ✅ | ✅ | 都包含 success/attempt/finalError |
| `session_info_changed` | ✅ | ✅ | 会话名称变更 |
| `thinking_level_changed` | ✅ | ✅ | 思考级别变更 |

**事件来源路径**：

```
Agent._emit
  └─> AgentSession 订阅
        ├─> Nova: EventController.handle() (packages/nova_harness/src/nova_harness/core/agent_session/controllers/event.py)
        └─> π:   _handleAgentEvent() (agent-session.ts:476)
              ├─> 先 _emitExtensionEvent(event) 给 ExtensionRunner
              └─> 再 _emit(event) 给外部监听器
```

Nova 把"Agent 事件处理"拆到了 `EventController`，π 直接内联在 `AgentSession`。功能等价。

---

### Bus 3：`ExtensionRunner` 扩展事件分发

| 项目 | 位置 | 核心实现 |
|---|---|---|
| Nova | `packages/nova_harness/src/nova_harness/core/extensions/runner.py:476` | `async def emit(self, event: Any)` |
| π | `packages/coding-agent/src/core/extensions/runner.ts:736` | `async emit<TEvent extends RunnerEmitEvent>(event)` |

**事件集合**：

| 事件类型 | Nova | π | 说明 |
|---|---|---|---|
| `project_trust` | ✅ | ✅ | 项目信任决策 |
| `resources_discover` | ✅ | ✅ | 扩展贡献资源路径 |
| `session_start` | ✅ | ✅ | 会话启动/加载/reload |
| `session_shutdown` | ✅ | ✅ | 会话关闭 |
| `session_before_switch` | ✅ | ✅ | 切换会话前（可取消） |
| `session_before_fork` | ✅ | ✅ | fork 前（可取消） |
| `session_before_compact` | ✅ | ✅ | 压缩前（可取消/定制） |
| `session_compact` | ✅ | ✅ | 压缩后 |
| `session_before_tree` | ✅ | ✅ | 树导航前 |
| `session_tree` | ✅ | ✅ | 树导航后 |
| `context` | ✅ | ✅ | 修改 LLM 上下文 |
| `before_provider_request` | ✅ | ✅ | 修改 provider payload |
| `after_provider_response` | ✅ | ✅ | 响应到达后 |
| `before_agent_start` | ✅ | ✅ | prompt 发送前 |
| `agent_start` | ✅ | ✅ | Agent 循环开始 |
| `agent_end` | ✅ | ✅ | Agent 循环结束 |
| `turn_start` | ✅ | ✅ | turn 开始 |
| `turn_end` | ✅ | ✅ | turn 结束 |
| `message_start` | ✅ | ✅ | 消息开始 |
| `message_update` | ✅ | ✅ | 消息流式更新 |
| `message_end` | ✅ | ✅ | 消息结束（可改写消息） |
| `tool_execution_start` | ✅ | ✅ | 工具执行开始 |
| `tool_execution_update` | ✅ | ✅ | 工具执行流式更新 |
| `tool_execution_end` | ✅ | ✅ | 工具执行结束 |
| `tool_call` | ✅ | ✅ | 工具调用前（可 block） |
| `tool_result` | ✅ | ✅ | 工具结果后（可改写） |
| `user_bash` | ✅ | ✅ | 用户手动执行 bash |
| `input` | ✅ | ✅ | 用户输入拦截 |
| `model_select` | ✅ | ✅ | 模型切换 |
| `thinking_level_select` | ✅ | ✅ | 思考级别切换 |

**关键差异**：

1. **ToolCallEvent / ToolResultEvent 的细化**
   - π 对内置工具做了按工具名区分的 discriminated union：
     - `BashToolCallEvent`, `ReadToolCallEvent`, `EditToolCallEvent`, ...
     - `BashToolResultEvent`, `ReadToolResultEvent`, ...
     - 提供 `isToolCallEventType("bash", event)` 等 type guard
   - Nova 使用统一的 `ToolCallEvent` / `ToolResultEvent`，通过 `tool_name` 字段区分。

2. **结果合并/链式处理**
   - `message_end`：两者都链式修改消息，并校验 role 不变。
   - `tool_result`：两者都链式修改 content/details/is_error。
   - `context`：两者都链式修改 messages。
   - `before_agent_start`：两者都支持返回 `messages` 和 `systemPrompt`。
   - `input`：两者都支持 `handled` 短路和 `transform` 链式转换。

3. **错误处理**
   - Nova：单个 handler 异常被吞掉，通过 `emit_error` 输出 `ExtensionErrorEvent`。
   - π：单个 handler 异常被吞掉，通过 `emitError` 输出 `ExtensionError`。

---

### Bus 4：扩展间通信总线

| 项目 | 位置 | 核心实现 |
|---|---|---|
| Nova | `packages/nova_harness/src/nova_harness/core/extensions/event_bus.py` | `class ExtensionEventBus` |
| π | `packages/coding-agent/src/core/event-bus.ts` | `createEventBus()` |

**Nova 实现**：

```python
class ExtensionEventBus:
    def on(self, event_type: str, handler: Callable[..., Any]) -> Callable[[], None]: ...
    def emit(self, event_type: str, *args: Any, **kwargs: Any) -> None: ...
    def clear(self) -> None: ...
```

- `on` 返回取消订阅函数
- `emit` 不收集返回值
- async handler 被 `asyncio.create_task` 调度
- 单个 handler 异常不影响其它 handler

**π 实现**：

```typescript
export interface EventBus {
    emit(channel: string, data: unknown): void;
    on(channel: string, handler: (data: unknown) => void): () => void;
}
export function createEventBus(): EventBusController { ... }
```

- 基于 `node:events`
- `on` 返回取消订阅函数
- handler 被 `async (data) => { try { await handler(data) } catch (err) { console.error(...) } }` 包装
- `emit` 不收集返回值

**暴露方式**：
- Nova：`NovaExtensionAPI.events = event_bus`（`packages/nova_harness/src/nova_harness/core/extensions/api.py:47`）
- π：`ExtensionAPI.events = eventBus`（`packages/coding-agent/src/core/extensions/loader.ts:325`）

**用途**：扩展之间通过总线发消息，例如一个扩展通知另一个扩展"主题已切换"、"模型已注册"等。该总线与 AgentSession/ExtensionRunner 的事件总线完全独立。

---

## 三、事件分流详细对比

下表列出所有事件在 **AgentSession 公开事件总线** 与 **ExtensionRunner 扩展事件总线** 之间的分流策略。`✅` 表示该总线会分发此事件，`❌` 表示不会，`专用` 表示有独立的 `emitXxx` 方法而非通用 `emit()`。

| 事件 | π AgentSession `_emit` | π ExtensionRunner | Nova AgentSession `_emit` | Nova ExtensionRunner | 是否一致 |
|---|---|---|---|---|---|
| **Agent 生命周期** |
| `agent_start` | ✅ | ✅（通用 `emit`） | ✅ | ✅（通用 `emit`） | ✅ |
| `agent_end` | ✅（改写 `willRetry`） | ✅（通用 `emit`） | ✅（补充 `will_retry`） | ✅（通用 `emit`） | ✅ |
| `before_agent_start` | ❌ | ✅（通用 `emit`，专用 `emitBeforeAgentStart`） | ❌ | ✅（通用 `emit`，专用 `emit_before_agent_start`） | ✅ |
| **Turn 生命周期** |
| `turn_start` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `turn_end` | ✅（π 附加 `turnIndex`） | ✅ | ✅ | ✅ | ✅ |
| `prepare_next_turn` | ❌ | ❌（π 无此扩展事件，只有 AgentLoopConfig hook） | ❌ | ✅（专用 `emit_prepare_next_turn`） | ⚠️ Nova 独有扩展事件 |
| `should_stop_after_turn` | ❌ | ❌（π 无此扩展事件，只有 AgentLoopConfig hook） | ❌ | ✅（专用 `emit_should_stop_after_turn`） | ⚠️ Nova 独有扩展事件 |
| **Message 生命周期** |
| `message_start` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `message_update` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `message_end` | ✅ | ✅（专用 `emitMessageEnd`） | ✅ | ✅（专用 `emit_message_end`） | ✅ |
| **Tool 执行生命周期** |
| `tool_execution_start` | ✅ | ❌ | ✅ | ❌ | ✅ |
| `tool_execution_update` | ✅ | ❌ | ✅ | ❌ | ✅ |
| `tool_execution_end` | ✅ | ❌ | ✅ | ❌ | ✅ |
| **Tool Hook 事件** |
| `tool_call` | ❌（Agent hook） | ✅（专用 `emitToolCall`） | ❌（Agent hook） | ✅（专用 `emit_tool_call`） | ✅ |
| `tool_result` | ❌（Agent hook） | ✅（专用 `emitToolResult`） | ❌（Agent hook） | ✅（专用 `emit_tool_result`） | ✅ |
| **Provider 请求 Hook** |
| `before_provider_request` | ❌ | ✅（专用 `emitBeforeProviderRequest`） | ❌ | ✅（专用 `emit_before_provider_request`） | ✅ |
| `after_provider_response` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`，专用 `emit_after_provider_response`） | ✅ |
| `context` | ❌ | ✅（专用 `emitContext`） | ❌ | ✅（专用 `emit_context`） | ✅ |
| **用户输入 / Bash** |
| `input` | ❌ | ✅（专用 `emitInput`） | ❌ | ✅（专用 `emit_input`） | ✅ |
| `user_bash` | ❌ | ✅（专用 `emitUserBash`） | ❌ | ✅（专用 `emit_user_bash`） | ✅ |
| **模型 / 思考级别** |
| `model_select` | ❌ | ✅（专用 `emitModelSelect`） | ❌ | ✅（专用 `emit_model_select`） | ✅ |
| `thinking_level_select` | ❌ | ✅（专用 `emitThinkingLevelSelect`） | ❌ | ✅（专用 `emit_thinking_level_select`） | ✅ |
| `thinking_level_changed` | ✅ | ❌ | ✅ | ❌ | ✅ |
| **会话生命周期** |
| `session_start` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_shutdown` | ❌ | ✅（通用 `emit`，专用 `emitSessionShutdownEvent`） | ❌ | ✅（通用 `emit`，专用 `emit_session_shutdown`） | ✅ |
| `session_before_switch` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_before_fork` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_before_compact` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_compact` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_before_tree` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_tree` | ❌ | ✅（通用 `emit`） | ❌ | ✅（通用 `emit`） | ✅ |
| `session_info_changed` | ✅ | ❌ | ✅ | ❌ | ✅ |
| **资源 / 信任 / 错误** |
| `resources_discover` | ❌ | ✅（专用 `emitResourcesDiscover`） | ❌ | ✅（专用 `emit_resources_discover`） | ✅ |
| `project_trust` | ❌ | ✅（顶层函数 `emitProjectTrustEvent`） | ❌ | ✅（专用 `emit_project_trust`） | ✅ |
| `extension_error` | ❌ | ❌（单独 `ExtensionErrorListener`） | ❌ | ❌（单独 `on_error`） | ✅ 都不属于通用事件流 |
| **AgentSession 内部状态** |
| `compaction_start` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `compaction_end` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `auto_retry_start` | ✅ | ❌ | ✅ | ❌ | ✅ |
| `auto_retry_end` | ✅ | ❌ | ✅ | ❌ | ✅ |
| `queue_update` | ✅ | ❌ | ✅ | ❌ | ✅ |

**说明**：

- 标 `❌` 不代表事件不存在，而是指该事件**不会进入对应总线的公开分发**。例如 `tool_call` / `tool_result` 通过 Agent 的 `before_tool_call` / `after_tool_call` hook 直接触发 ExtensionRunner 的专用方法，不经过 AgentSession 公开流。
- `extension_error` 两边都走独立的错误监听通道，不进通用事件分发。
- **结论**：两套代码的事件分流策略完全同步。`tool_execution_update` 不进 ExtensionRunner，但会进 AgentSession 公开事件，这是前后端能拿到流式工具输出的关键。

---

## 四、工具执行事件的生命周期

工具执行事件在 Agent 循环中产生，对 UI 流式渲染至关重要。

### π 的工具执行流程

```
Agent loop
  ├─> tool_execution_start
  │     ├─> AgentSession._emitExtensionEvent → ExtensionRunner.emit({type: "tool_execution_start"})
  │     └─> AgentSession._emit → TUI/RPC 监听器
  ├─> tool_execution_update (流式输出)
  │     ├─> AgentSession._emitExtensionEvent → ExtensionRunner.emit({type: "tool_execution_update"})
  │     └─> AgentSession._emit → TUI/RPC 监听器
  └─> tool_execution_end
        ├─> AgentSession._emitExtensionEvent → ExtensionRunner.emit({type: "tool_execution_end"})
        └─> AgentSession._emit → TUI/RPC 监听器
```

π 的 TUI 在 `interactive-mode.ts` 中直接订阅 `AgentSessionEvent`，遇到 `tool_execution_update` 就更新对应 `ToolExecutionComponent`。

π 的 RPC 模式在 `rpc-mode.ts:354` 中：

```typescript
unsubscribe = session.subscribe((event) => {
    output(event);
});
```

直接输出所有 AgentSessionEvent。

### Nova 的工具执行流程

```
Agent loop
  └─> Agent._emit(tool_execution_*)
        └─> AgentSession EventController.handle()
              └─> AgentSession._emit(event)
                    ├─> 外部监听器（NovaServer._subscribe_session）
                    │     └─> map_event(event) → UIEvent
                    │           └─> JSON-RPC agent/event
                    └─> ExtensionRunner.emit(event)（但 tool_execution_* 在 Nova 中也不触发 ExtensionRunner）
```

**关键缺口**：`packages/nova_harness/src/nova_harness/core/ui/mapper.py:112` 的 `map_event` 函数处理了 `ToolExecutionStartEvent` 和 `ToolExecutionEndEvent`，但没有处理 `ToolExecutionUpdateEvent`。这导致 RPC/WebSocket 前端看不到 bash 等工具的流式输出。

---

## 五、RPC / 前端输出对比

### π：直接输出 AgentSessionEvent

```typescript
// packages/coding-agent/src/modes/rpc/rpc-mode.ts:354
unsubscribe = session.subscribe((event) => {
    output(event);
});
```

优点：
- 只要事件进了 `AgentSession._emit()`，前端就能看到。
- 没有 mapper 遗漏问题。

缺点：
- 前端协议与内部事件结构深度耦合。
- 内部事件改名/改字段会直接影响前端。
- RPC 模式只能输出会话事件，不能输出 TUI 专属渲染事件（除非通过 extension_ui_request）。

### Nova：内部事件 → UIEvent mapper → JSON-RPC

```python
# packages/nova_harness/src/nova_harness/core/server.py:73
def listener(event: Any) -> None:
    registry = getattr(session, "block_registry", None)
    ui_event = map_event(event, registry)
    if ui_event is not None:
        asyncio.create_task(self.send_event(ui_event.model_dump()))
```

优点：
- 前后端解耦，前端依赖稳定的 `UIEvent` schema。
- 可以在 mapper 层做缓冲/过滤/聚合（如合并密集的 `tool_execution_update`）。
- 可以引入 `StreamingPolicy` 控制是否流式输出。

缺点：
- mapper 必须手动维护，遗漏事件会导致前端看不到。
- 当前 `ToolExecutionUpdateEvent` 未映射。
- `UIEvent` 类型还不够完整（缺少 `session` 事件的部分字段）。

### 当前 Nova UIEvent 类型

```python
# packages/nova_harness/src/nova_harness/core/ui/events.py
UIEvent = Union[
    MessageStartEvent,      # message_start
    MessageDeltaEvent,      # message_delta
    MessageEndEvent,        # message_end
    ToolCallEvent,          # tool_call
    ToolOutputEvent,        # tool_output
    ToolResultEvent,        # tool_result
    StatusEvent,            # status
    SessionEvent,           # session
    ErrorEvent,             # error
]
```

**映射关系**（按 mapper.py 当前实现）：

| 内部事件 | Nova UIEvent | π 输出 |
|---|---|---|
| `agent_start` | ❌ 未映射 | 原样 `agent_start` |
| `agent_end` | ❌ 未映射 | 原样 `agent_end`（含 `willRetry`） |
| `turn_start` | ❌ 未映射 | 原样 `turn_start` |
| `turn_end` | ❌ 未映射 | 原样 `turn_end` |
| `message_start` | `MessageStartEvent` | 原样 `message_start` |
| `message_update` | `MessageDeltaEvent` | 原样 `message_update` |
| `message_end` | `MessageEndEvent` | 原样 `message_end` |
| `tool_execution_start` | `ToolCallEvent` | 原样 `tool_execution_start` |
| `tool_execution_update` | ❌ 未映射 | 原样 `tool_execution_update` |
| `tool_execution_end` | `ToolResultEvent` | 原样 `tool_execution_end` |
| `tool_call` | `ToolCallEvent` | 专用 ExtensionRunner 事件 |
| `tool_result` | `ToolResultEvent` | 专用 ExtensionRunner 事件 |
| `compaction_start` | `StatusEvent(kind="compacting")` | 原样 `compaction_start` |
| `compaction_end` | `StatusEvent(kind="idle")` | 原样 `compaction_end` |
| `auto_retry_start` | `StatusEvent(kind="working")` | 原样 `auto_retry_start` |
| `auto_retry_end` | `StatusEvent(kind="idle")` | 原样 `auto_retry_end` |
| `queue_update` | ❌ 未映射 | 原样 `queue_update` |
| `session_info_changed` | ❌ 未映射 | 原样 `session_info_changed` |
| `thinking_level_changed` | ❌ 未映射 | 原样 `thinking_level_changed` |

---

## 六、UIContext / 扩展 UI 原语对比

### π 的 `ExtensionUIContext`

`packages/coding-agent/src/core/extensions/types.ts:124`

提供的能力：
- 对话框：`select`, `confirm`, `input`, `editor`, `custom`
- 通知：`notify`
- 终端输入：`onTerminalInput`
- 状态/工作指示器：`setStatus`, `setWorkingMessage`, `setWorkingVisible`, `setWorkingIndicator`, `setHiddenThinkingLabel`
- 组件区域：`setWidget`, `setFooter`, `setHeader`
- 编辑器：`pasteToEditor`, `setEditorText`, `getEditorText`, `setEditorComponent`, `getEditorComponent`, `addAutocompleteProvider`
- 主题：`theme`, `getAllThemes`, `getTheme`, `setTheme`
- 工具展开：`getToolsExpanded`, `setToolsExpanded`

RPC 模式只实现了其中一部分（`select/confirm/input/editor/notify/setStatus/setWidget/setTitle/setEditorText`），TUI 专属方法被注释为"RPC 不支持"。

### Nova 的 `UIContext`

`packages/nova_harness/src/nova_harness/core/ui/context.py`

抽象基类，提供：
- `capabilities: Set[str]`
- `request(method, params) -> UIResponse`
- `notify(method, params)`
- `set_component(region, component)`, `patch_component(key, props)`, `remove_component(key)`
- 便捷方法：`select`, `confirm`, `input`, `editor`, `custom`
- 同步 getter：`get_editor_text`, `get_all_themes`, `get_theme`, `get_tools_expanded`
- 终端输入：`on_terminal_input`

`TransportUIContext` 把这些能力通过 JSON-RPC 转发给前端：
- `ui/request`：需要响应的请求
- `ui/notify`：不需要响应的通知
- `ui/component/set` / `ui/component/patch` / `ui/component/remove`：组件操作

**关键区别**：
- π 的 UIContext 方法名和语义与 TUI 强绑定，方法数量多但 frontend 契约复杂。
- Nova 的 UIContext 更通用，把具体 UI 方法抽象为 `request/notify/component`，适合前后端分离。但当前只暴露了有限的前端能力，TUI 专属能力（如工作指示器、widget 区域）需要在前端协议中进一步补齐。

---

## 七、扩展加载与事件总线的绑定

### π

```typescript
// resource-loader.ts:214
this.eventBus = options.eventBus ?? createEventBus();

// extensions/loader.ts:423
const resolvedEventBus = eventBus ?? createEventBus();
const resolvedRuntime = runtime ?? createExtensionRuntime();

// extensions/loader.ts:383
const api = createExtensionAPI(extension, runtime, cwd, eventBus);
```

`DefaultResourceLoader` 内部自建 `EventBus`，并通过 `createExtensionAPI` 把它作为 `api.events` 暴露给每个扩展。

### Nova

```python
# core/extensions/loader.py:321
resolved_event_bus = event_bus or ExtensionEventBus()
resolved_runtime = runtime or ExtensionRuntime(
    cwd=cwd, event_bus=resolved_event_bus
)
resolved_api_factory = api_factory or (
    lambda ext, rt: NovaExtensionAPI(ext, rt, cwd=cwd, event_bus=resolved_event_bus)
)
```

Nova 的 `ExtensionRuntime` 和 `NovaExtensionAPI` 都持有同一个 `ExtensionEventBus`。扩展通过 `api.events.on/emit` 进行扩展间通信。

**差异**：
- π 的 EventBus 使用 `node:events`，handler 签名 `(data: unknown) => void`。
- Nova 的 ExtensionEventBus 使用 Python dict，handler 签名 `(*args, **kwargs) => Any`。
- 两者都不收集返回值，都支持取消订阅。

---

## 八、关键差异总结

### 1. ToolCallEvent / ToolResultEvent 的细化程度

π 为每个内置工具定义了独立的事件类型，并提供 type guard。Nova 使用统一类型。这导致：
- π 的扩展可以针对 `BashToolCallEvent` 做精确类型匹配。
- Nova 的扩展只能通过 `event.tool_name == "bash"` 判断。

**影响**：功能等价，但 π 的类型安全性更好。

### 2. `agent_end` 的处理

π 与 Nova 都在 AgentSession 层改写 `agent_end`：π 附加 `willRetry`，Nova 附加 `will_retry`。两者都把重试信息暴露给前端监听器。

### 3. `tool_execution_update` 的 UI 映射

π 直接输出 `tool_execution_update`，前端能拿到 bash 流式输出。
Nova 的 `map_event` 没有处理 `ToolExecutionUpdateEvent`，这是当前最紧迫的 UI 缺口。

### 4. 扩展 UI 原语的 RPC 兼容性

π 的 RPC 模式只实现了部分 UI 原语，大量 TUI 方法被注释为不支持。
Nova 的 UIContext 更抽象，但当前实现也缺少工作指示器、widget 区域等组件协议。

### 5. `prepare_next_turn` / `should_stop_after_turn` 扩展事件

Nova 把这两个 Agent loop hook 暴露为扩展事件，扩展可以通过 `on("prepare_next_turn")` / `on("should_stop_after_turn")` 参与决策。
π 没有对应的扩展事件，这两个能力只在 `AgentLoopConfig` 层面由调用方直接设置。

**影响**：Nova 的扩展生态更强大，但需要确保这两个事件的文档和测试覆盖。

### 6. 扩展发现

π 支持 `package.json#pi.extensions` 声明多个扩展入口。
Nova 当前只支持 `extension.py` / `__init__.py` / 直接 `.py` 文件作为入口，不支持多入口声明。

---

## 九、对 Nova 的启示与建议

### 9.1 是否需要合并总线？

不需要。π 也保持了 4 层总线，说明这个分层是有意的：

1. **Agent 总线**：底层 Agent 循环事件，给 AgentSession 消费。
2. **AgentSession 公开事件**：给 UI/前端/持久化。
3. **ExtensionRunner**：扩展 Hook 事件，可以拦截、修改、取消。
4. **ExtensionEventBus**：扩展间通信。

Nova 不应该把扩展事件强行合并进 `AgentSession._emit()`，而是应该：
- 明确哪些事件属于"会话公开事件"（进 `AgentSession._emit()`）。
- 明确哪些事件属于"扩展 Hook 事件"（进 `ExtensionRunner`）。
- UI mapper 只订阅 `AgentSession._emit()`。

### 9.2 最优先补齐：tool_execution_update 映射

参考 π 的行为，Nova 应该在 `core/ui/mapper.py` 中增加：

```python
if isinstance(internal, ToolExecutionUpdateEvent):
    return ToolOutputEvent(
        call_id=internal.tool_call_id or "",
        tool_name=internal.tool_name or "",
        chunk=...,  # 从 internal.partial_result 提取
        stream="stdout" or "stderr",
        is_partial=True,
    )
```

同时改造 bash executor 的 `OutputAccumulator`，把 stdout/stderr 分别推送为 `tool_execution_update` 事件。

### 9.3 补齐 UIEvent 覆盖范围

建议把以下 AgentSession 事件也映射为 UIEvent：

| 内部事件 | 建议 UIEvent |
|---|---|
| `queue_update` | `QueueUpdateEvent`（新增） |
| `session_info_changed` | `SessionEvent(kind="renamed")` |
| `thinking_level_changed` | `SessionEvent` 或新增 `ThinkingLevelEvent` |

### 9.4 可选：引入 StreamingPolicy

Nova 当前已经具备 `UIEvent` 抽象，这是比 π 更强的地方。可以进一步引入 `StreamingPolicy`：
- `realtime`：每个内部事件立即映射输出。
- `buffered`：按时间/大小缓冲后输出。
- `none`：只输出最终结果。

这在 Web UI 场景下比 π 更灵活。

### 9.5 补齐扩展 UI 原语

Nova 的 `UIContext` 抽象已经比 π 更适合前后端分离。建议继续补齐：
- 工作指示器：`setWorkingMessage`, `setWorkingVisible`, `setWorkingIndicator`
- 隐藏思考标签：`setHiddenThinkingLabel`
- Widget 区域：`setWidget`
- 页眉/页脚：`setHeader`, `setFooter`
- 终端输入：`onTerminalInput`

这些可以通过 `ui/notify` 或 `ui/component/*` 协议暴露给前端。

---

## 十、总结

| 项目 | 总线架构 | RPC 输出 | 当前主要问题 |
|---|---|---|---|
| π (TS) | 4 层总线 | 直接输出 AgentSessionEvent | 前端协议与内部事件耦合；RPC 不支持完整 TUI 原语 |
| Nova (Python) | 4 层总线 | 内部事件 → UIEvent → JSON-RPC | mapper 不完整，缺少 `tool_execution_update` 映射；UIEvent 覆盖不全 |

**Nova 不需要推倒重来**。参考 π 可以确认：

- 4 层总线设计是合理的。
- `tool_execution_update` 应该被前端消费。
- AgentSession 公开事件集已经覆盖了应公开的事件。
- 真正要修的是 `mapper` 和 `UIEvent` 协议完整性，以及扩展 UI 原语的 RPC 兼容性。
