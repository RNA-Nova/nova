# Nova 事件总线对比

> 范围：`nova_harness` 及其依赖的 `nova_agent` 中的事件总线系统  
> 目的：厘清三套总线各自承载的事件类型，找出重叠与缺口

---

## 总线总览

| 总线 | 位置 | 核心 API | 用途 |
|---|---|---|---|
| **Bus 1: Agent 内部总线** | `nova_agent/agent.py` | `subscribe(fn)` / `emit(event)` | Agent 循环内部状态通知 |
| **Bus 2: AgentSession 内部总线** | `nova_harness/core/agent_session/agent.py` | `subscribe(listener)` / `_emit(event)` | 会话级状态通知；UI 层订阅这里 |
| **Bus 3: ExtensionRunner 分发** | `nova_harness/core/extensions/runner.py` | `has_handlers(type)` / `emit(event)` | 把事件路由到注册了 handler 的扩展 |

> 注：还有 `ExtensionEventBus`（扩展间通信），因只用于扩展之间，未列入本表。

---

## 事件类型对比表

| 事件类型 | Bus 1 (Agent) | Bus 2 (AgentSession) | Bus 3 (ExtensionRunner) | 当前是否映射到 UI | 备注 |
|---|---|---|---|---|---|
| `agent_start` | ✅ | 透传自 Bus 1 | ✅ | ❌ | 扩展可拦截 |
| `agent_end` | ✅ | 透传自 Bus 1 | ✅ | ❌ | 扩展可拦截 |
| `turn_start` | ✅ | 透传自 Bus 1 | ✅ | ❌ | 扩展可拦截 |
| `turn_end` | ✅ | 透传自 Bus 1 | ✅ | ❌ | 扩展可拦截 |
| `message_start` | ✅ | 透传自 Bus 1 | ✅ | ✅ `message_start` | 核心消息流 |
| `message_update` | ✅ | 透传自 Bus 1 | ❌ | ✅ `message_delta` | 核心消息流 |
| `message_end` | ✅ | 透传自 Bus 1 | ✅ | ✅ `message_end` | 核心消息流 |
| `tool_execution_start` | ✅ | 透传自 Bus 1 | ❌ | ✅ `tool_call` | 核心工具流 |
| `tool_execution_update` | ✅ | 透传自 Bus 1 | ❌ | ❌ | **未映射到 UI** |
| `tool_execution_end` | ✅ | 透传自 Bus 1 | ❌ | ✅ `tool_result` | 核心工具流 |
| `tool_call` | ❌ | ✅ 会话层补充 | ✅ | ✅ `tool_call` | Agent assistant message 里的 tool call |
| `tool_result` | ❌ | ✅ 会话层补充 | ✅ | ✅ `tool_result` | 工具结果消息 |
| `input` | ❌ | ❌ | ✅ | ❌ | 用户输入事件，扩展可拦截 |
| `user_bash` | ❌ | ❌ | ✅ | ❌ | 用户 bash 命令，扩展可拦截 |
| `context` | ❌ | ❌ | ✅ | ❌ | 上下文转换事件 |
| `before_provider_request` | ❌ | ❌ | ✅ | ❌ | provider 请求前拦截 |
| `after_provider_response` | ❌ | ❌ | ✅ | ❌ | provider 响应后通知 |
| `before_agent_start` | ❌ | ❌ | ✅ | ❌ | agent 启动前拦截 |
| `prepare_next_turn` | ❌ | ❌ | ✅ | ❌ | 扩展可修改下一步 |
| `should_stop_after_turn` | ❌ | ❌ | ✅ | ❌ | 扩展可决定是否停止 |
| `resources_discover` | ❌ | ❌ | ✅ | ❌ | 资源发现事件 |
| `model_select` | ❌ | ❌ | ✅ | ❌ | **模型切换；AgentSession 总线缺失** |
| `thinking_level_select` | ❌ | ❌ | ✅ | ❌ | 思考级别切换；扩展专用 |
| `thinking_level_changed` | ❌ | ✅ | ❌ | ❌ | AgentSession 总线冗余事件 |
| `session_info_changed` | ❌ | ✅ | ❌ | ❌ | 会话重命名；**未映射到 UI** |
| `session_start` | ❌ | ❌ | ✅ | ❌ | 新会话/切换/fork；**未映射到 UI** |
| `session_shutdown` | ❌ | ❌ | ✅ | ❌ | 会话关闭 |
| `session_before_switch` | ❌ | ❌ | ✅ | ❌ | 切换前拦截 |
| `session_before_fork` | ❌ | ❌ | ✅ | ❌ | fork 前拦截 |
| `session_before_compact` | ❌ | ❌ | ✅ | ❌ | 压缩前拦截 |
| `session_compact` | ❌ | ❌ | ✅ | ❌ | 压缩完成；**未映射到 UI** |
| `session_before_tree` | ❌ | ❌ | ✅ | ❌ | 树导航前拦截 |
| `session_tree` | ❌ | ❌ | ✅ | ❌ | 树导航完成 |
| `compaction_start` | ❌ | ✅ | ✅ | ✅ `status` | 手动/自动压缩开始 |
| `compaction_end` | ❌ | ✅ | ✅ | ✅ `status` | 手动/自动压缩结束 |
| `auto_compaction_start` | ❌ | ✅ | ❌ | ✅ `status` | 自动压缩开始 |
| `auto_compaction_end` | ❌ | ✅ | ❌ | ✅ `status` | 自动压缩结束 |
| `auto_retry_start` | ❌ | ✅ | ❌ | ✅ `status` | 自动重试开始 |
| `auto_retry_end` | ❌ | ✅ | ❌ | ✅ `status` | 自动重试结束 |
| `queue_update` | ❌ | ✅ | ❌ | ❌ | 消息队列更新 |
| `extension_error` | ❌ | ❌ | ✅ | ❌ | 扩展错误 |

---

## 关键发现

### 1. Bus 1 → Bus 2 的透传关系

`AgentSession` 的 `EventController` 订阅了 `nova_agent.Agent` 的事件，然后选择性地重新 `_emit` 到 `AgentSession` 总线：

```python
self._unsubscribe_agent = self.agent.subscribe(self._events.handle)
```

所以 Bus 1 里的事件只有被 `EventController` 转发后，才会进入 Bus 2。

### 2. Bus 3（ExtensionRunner）是独立通道

`ExtensionRunner.emit()` 不依赖 Bus 1 或 Bus 2。很多事件（如 `session_start`、`model_select`）直接调用了 `runner.emit()`，但**没有**同时 `AgentSession._emit()`。

这导致 UI 层（订阅 Bus 2）看不到这些事件。

### 3. 当前 UI 映射的缺口

| 已映射到 UI | 未映射到 UI |
|---|---|
| `message_start/delta/end` | `tool_execution_update` |
| `tool_call` / `tool_result` | `session_start` / `session_info_changed` / `session_compact` |
| `status`（compaction/retry） | `model_select` |
| | `agent_start/end`、`turn_start/end` |
| | 大量扩展事件 |

### 4. 冗余事件

- `thinking_level_changed` 只走 Bus 2，没有消费者。
- `thinking_level_select` 只走 Bus 3，用于扩展。
- 两者语义几乎相同，应该合并。

---

## 建议的合并方向

目标：**所有事件只走一条主总线（Bus 2 / AgentSession）**，`ExtensionRunner` 作为该总线的一个订阅者。

| 当前 | 建议 |
|---|---|
| `ModelController.set_model()` 只调 `runner.emit_model_select()` | 改为 `self._session._emit(ModelSelectEvent(...))`，扩展 runner 订阅处理 |
| `AgentSession.new_session()` 等只调 `runner.emit(session_start)` | 改为 `self._session._emit(session_start)`，扩展 runner 订阅处理 |
| `ModelController.set_thinking_level()` 同时 emit 两个事件 | 统一为 `ThinkingLevelSelectEvent` 或 `ThinkingLevelChangedEvent` 一个事件 |
| `EventController` 手动转发 Agent 事件 | 保留，但确保所有需要的事件都转发 |

合并后：

```
AgentSession._emit(event)
  ├── NovaServer 订阅 → map_event → UI 前端
  ├── ExtensionRunner 订阅 → 扩展 handler
  └── 内部 controllers 订阅
```

这样 UI 层自然就能看到完整事件流，不需要再维护独立的扩展事件通道。
