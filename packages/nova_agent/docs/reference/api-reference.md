# API 参考

本文档是 `nova_agent` 公共 API 的完整速查表，涵盖 `Agent` 类、低层 loop 入口、事件类型、Hook、核心数据类型与工具验证函数。所有类型均可通过 `from nova_agent import ...` 直接导入。

---

## 顶层导出速览

```python
from nova_agent import (
    # 主要类
    Agent,
    AgentEventStream,

    # Agent loop 入口
    agent_loop,
    agent_loop_continue,
    run_agent_loop,
    run_agent_loop_continue,

    # 事件类型
    AgentEvent,
    AgentStartEvent,
    AgentEndEvent,
    TurnStartEvent,
    TurnEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    MessageEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    ToolExecutionEndEvent,

    # 核心数据类型
    AgentMessage,
    AgentContext,
    AgentState,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    AgentToolCall,
    CustomAgentMessage,

    # 类型别名与枚举
    ModelThinkingLevel,
    StreamFn,
    AgentToolUpdateCallback,
    ToolExecutionMode,
    QueueMode,

    # Hook 上下文与结果
    BeforeToolCallContext,
    BeforeToolCallResult,
    AfterToolCallContext,
    AfterToolCallResult,
    ShouldStopAfterTurnContext,
    PrepareNextTurnContext,
    AgentLoopTurnUpdate,

    # 工具函数
    validate_tool_call,
    validate_tool_arguments,
    set_validation_enabled,
    clear_validator_cache,

    # 中断信号
    AbortSignal,
)
```

---

## `Agent` 类

`Agent` 是会话级入口，封装状态管理、事件订阅、消息队列与生命周期控制。内部调用 `agent_loop` 完成 turn 循环。

### 构造函数

```python
Agent(
    initial_state: Optional[AgentState | dict] = None,
    convert_to_llm: Optional[Callable[[List[AgentMessage]], List[Message] | Awaitable[List[Message]]]] = None,
    transform_context: Optional[Callable[[List[AgentMessage], Optional[AbortSignal]], Awaitable[List[AgentMessage]]]] = None,
    steering_mode: QueueMode = "one-at-a-time",
    follow_up_mode: QueueMode = "one-at-a-time",
    stream_fn: Optional[StreamFn] = None,
    session_id: Optional[str] = None,
    get_api_key: Optional[Callable[[str], Optional[str] | Awaitable[Optional[str]]]] = None,
    thinking_budgets: Optional[ThinkingBudgets] = None,
    transport: Transport = "sse",
    max_retry_delay_ms: Optional[int] = None,
    tool_execution: ToolExecutionMode = "parallel",
    on_payload: Optional[Callable[[Any], None]] = None,
    on_response: Optional[Callable[[ProviderResponse, Any], None]] = None,
    before_tool_call: Optional[Callable[[BeforeToolCallContext, Optional[AbortSignal]], BeforeToolCallResult | Awaitable[BeforeToolCallResult] | None]] = None,
    after_tool_call: Optional[Callable[[AfterToolCallContext, Optional[AbortSignal]], AfterToolCallResult | Awaitable[AfterToolCallResult] | None]] = None,
    prepare_next_turn: Optional[Callable[[PrepareNextTurnContext], AgentLoopTurnUpdate | Awaitable[AgentLoopTurnUpdate] | None]] = None,
    should_stop_after_turn: Optional[Callable[[ShouldStopAfterTurnContext], bool | Awaitable[bool]]] = None,
)
```

| 参数 | 说明 |
|------|------|
| `initial_state` | 初始状态，可传入 `AgentState` 或字典，未提供的字段使用默认值 |
| `convert_to_llm` | 把 `AgentMessage[]` 转成 LLM 可识别的 `Message[]` |
| `transform_context` | 在 `convert_to_llm` 之前对上下文进行变换（如裁剪、注入外部上下文） |
| `steering_mode` / `follow_up_mode` | 队列 drain 模式：`"all"` 一次性全部取出，`"one-at-a-time"` 每次取一条 |
| `stream_fn` | LLM stream 函数，默认使用 `nova_ai.stream_simple` |
| `session_id` | 会话 ID，会透传给 LLM 请求 |
| `get_api_key` | 动态解析 API key，适用于短期 OAuth token |
| `thinking_budgets` / `transport` / `max_retry_delay_ms` | 透传给 `nova_ai` 的流式选项 |
| `tool_execution` | 工具执行策略：`"parallel"`（默认）或 `"sequential"` |
| `on_payload` / `on_response` | 原始请求/响应回调 |
| `before_tool_call` / `after_tool_call` | 工具执行前后 Hook |
| `prepare_next_turn` | 每轮结束后修改下一轮上下文/模型/思考级别 |
| `should_stop_after_turn` | 每轮结束后决定是否停止循环 |

### 属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `state` | `AgentState` | 当前完整状态（只读引用） |
| `signal` | `Optional[AbortSignal]` | 当前 run 的中断信号 |
| `session_id` | `Optional[str]` | 会话 ID |
| `thinking_budgets` | `Optional[ThinkingBudgets]` | 思考预算配置 |
| `transport` | `Transport` | 传输方式，默认 `"sse"` |

### 状态变更方法

| 方法 | 说明 |
|------|------|
| `set_model(model: Model)` | 设置模型 |
| `set_thinking_level(level: ModelThinkingLevel)` | 设置思考级别 |
| `set_system_prompt(value: str)` | 设置系统提示词 |
| `set_tools(tools: List[AgentTool])` | 设置工具列表 |
| `replace_messages(messages: List[AgentMessage])` | 替换历史消息 |
| `append_message(message: AgentMessage)` | 追加一条消息 |
| `clear_messages()` | 清空历史消息 |
| `reset()` | 重置状态、清空消息与队列、清除错误 |

### 队列方法

| 方法 | 说明 |
|------|------|
| `steer(message: AgentMessage)` | 向 steering 队列插入消息，可在 run 中中断当前 batch 并注入 |
| `follow_up(message: AgentMessage)` | 向 follow_up 队列插入消息，在 agent 空闲后继续处理 |
| `clear_steering_queue()` | 清空 steering 队列 |
| `clear_follow_up_queue()` | 清空 follow_up 队列 |
| `clear_all_queues()` | 同时清空两个队列 |
| `has_queued_messages()` | 是否有待处理队列消息 |
| `set_steering_mode(mode: QueueMode)` / `get_steering_mode()` | 设置/获取 steering drain 模式 |
| `set_follow_up_mode(mode: QueueMode)` / `get_follow_up_mode()` | 设置/获取 follow_up drain 模式 |

### 运行控制方法

| 方法 | 说明 |
|------|------|
| `prompt(input, images=None)` | 发起一次新的对话。`input` 可为 `str`、`AgentMessage` 或 `AgentMessage[]` |
| `continue_()` | 从当前上下文继续。若最后一条为 assistant 消息，会先尝试 drain 队列；否则继续未完成的 loop |
| `abort()` | 中断当前 run，底层 stream 会收到 `stop_reason="aborted"` |
| `wait_for_idle()` | 等待当前 run 结束 |
| `subscribe(listener: AgentListener) -> Callable[[], None]` | 订阅事件，返回取消订阅函数 |

---

## Agent Loop 入口

除了面向对象的 `Agent`，`nova_agent` 也暴露了两组低层 loop API：

| 函数 | 说明 |
|------|------|
| `run_agent_loop(prompts, context, config, sink, signal=None, stream_fn=None)` | 同步风格（仍须 await）的完整 loop，传入事件 sink |
| `run_agent_loop_continue(context, config, sink, signal=None, stream_fn=None)` | 在不新增 prompt 的情况下继续已有上下文 |
| `agent_loop(prompts, context, config, signal=None, stream_fn=None) -> AgentEventStream` | 返回 `AgentEventStream` 的异步流式入口，内部创建后台任务 |
| `agent_loop_continue(context, config, signal=None, stream_fn=None) -> AgentEventStream` | 对应 `run_agent_loop_continue` 的流式入口 |

`AgentEventStream` 继承自 `nova_ai.EventStream[AgentEvent, List[AgentMessage]]`，可用 `async for event in stream` 迭代，或通过 `await stream.get_result()` 获取最终结果（`agent_end` 携带的 `messages`）。

---

## `AgentTool` 与 `AgentToolResult`

### 自定义工具

```python
class MyTool(AgentTool[MyParams, MyDetails]):
    name: str = "my_tool"
    description: str = "工具描述"
    parameters: dict = {...}          # JSON Schema
    label: str = "显示名称"
    execution_mode: ToolExecutionMode = "parallel"

    def prepare_arguments(self, args: Any) -> Any:
        # 在 schema 验证前对参数做兼容性转换
        return args

    async def execute(
        self,
        tool_call_id: str,
        params: MyParams,
        signal: Optional[Any] = None,
        on_update: Optional[Any] = None,
    ) -> AgentToolResult[MyDetails]:
        ...
```

| 字段/方法 | 说明 |
|-----------|------|
| `name` / `description` / `parameters` | 继承自 `nova_ai.Tool` 的标准定义 |
| `label` | UI 显示名称 |
| `execution_mode` | 单工具级执行模式覆盖，未设置时使用 `AgentLoopConfig.tool_execution` |
| `prepare_arguments(args)` | 验证前参数兼容层，须返回匹配 `TParameters` 的对象 |
| `execute(...)` | 实际执行逻辑，必须返回 `AgentToolResult` |

### `AgentToolResult[TDetails]`

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `List[TextContent \| ImageContent]` | 返回给 LLM 的内容块 |
| `details` | `TDetails` | 结构化详情，可用于 UI 展示或日志 |
| `terminate` | `Optional[bool]` | 提示当前 tool batch 是否应提前终止；**仅当 batch 内所有结果都置为 `True` 时才生效** |

---

## 消息、上下文与状态

### `AgentMessage`

```python
AgentMessage = Union[Message, CustomAgentMessage]
```

标准 `nova_ai.Message`（`UserMessage`、`AssistantMessage`、`ToolResultMessage` 等）或继承 `CustomAgentMessage` 的自定义类型。自定义消息默认会被 `_default_convert_to_llm` 过滤掉。

### `CustomAgentMessage`

自定义消息基类，只继承 `NovaBaseModel`，不含额外字段。适合 UI 通知、占位消息等非 LLM 消息。

### `AgentContext`

| 字段 | 类型 | 说明 |
|------|------|------|
| `system_prompt` | `Optional[str]` | 系统提示词 |
| `messages` | `List[AgentMessage]` | 当前对话历史 |
| `tools` | `Optional[List[Any]]` | 当前可用工具 |

### `AgentState`

| 字段 | 类型 | 说明 |
|------|------|------|
| `system_prompt` | `Optional[str]` | 系统提示词 |
| `model` | `Optional[Model]` | 当前模型 |
| `thinking_level` | `ModelThinkingLevel` | 思考级别（默认 `OFF`） |
| `tools` | `List[Any]` | 工具列表 |
| `messages` | `List[AgentMessage]` | 对话历史 |
| `is_streaming` | `bool` | 是否正在运行 |
| `streaming_message` | `Optional[AgentMessage]` | 当前正在流式生成的消息 |
| `pending_tool_calls` | `Set[str]` | 正在执行中的 tool_call_id 集合 |
| `error_message` | `Optional[str]` | 最近一次 run 的错误信息 |

---

## `AgentLoopConfig`

`AgentLoopConfig` 是 dataclass，通过组合持有 `stream_options: SimpleStreamOptions`（传给 `nova_ai` 的纯数据选项，含 `reasoning`、`session_id`、`transport`、`thinking_budgets`、`max_retry_delay_ms`、`on_payload`、`on_response` 等字段），下表所列为其自身的运行时回调与策略字段。

| 字段 | 类型 | 说明 |
|------|------|------|
| `model` | `Model` | 要调用的 LLM 模型 |
| `convert_to_llm` | `Callable[[List[AgentMessage]], List[Message] \| Awaitable[List[Message]]]` | 消息转换函数 |
| `transform_context` | `Callable[[List[AgentMessage], Optional[Any]], Awaitable[List[AgentMessage]]]` | 上下文变换 |
| `get_api_key` | `Callable[[str], Optional[str] \| Awaitable[Optional[str]]]` | 动态 API key |
| `should_stop_after_turn` | `Callable[[ShouldStopAfterTurnContext], bool \| Awaitable[bool]]` | 每轮结束后决定是否停止 |
| `prepare_next_turn` | `Callable[[PrepareNextTurnContext], AgentLoopTurnUpdate \| Awaitable[AgentLoopTurnUpdate] \| None]` | 准备下一轮状态 |
| `get_steering_messages` | `Callable[[], Awaitable[List[AgentMessage]]]` | 获取 steering 消息 |
| `get_follow_up_messages` | `Callable[[], Awaitable[List[AgentMessage]]]` | 获取 follow_up 消息 |
| `tool_execution` | `ToolExecutionMode` | `"parallel"` 或 `"sequential"` |
| `before_tool_call` | `Callable[[BeforeToolCallContext, Optional[Any]], ...]` | 工具执行前 Hook |
| `after_tool_call` | `Callable[[AfterToolCallContext, Optional[Any]], ...]` | 工具执行后 Hook |
| `stream_fn` | `Optional[Any]` | LLM stream 函数覆盖 |

---

## Hook 机制

### `BeforeToolCallContext`

| 字段 | 类型 | 说明 |
|------|------|------|
| `assistant_message` | `AssistantMessage` | 触发工具调用的 assistant 消息 |
| `tool_call` | `AgentToolCall` | 当前工具调用 |
| `args` | `Any` | 已验证的参数 |
| `context` | `AgentContext` | 当前上下文 |

### `BeforeToolCallResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| `block` | `Optional[bool]` | 是否阻止执行 |
| `reason` | `Optional[str]` | 阻止原因 |

### `AfterToolCallContext`

| 字段 | 类型 | 说明 |
|------|------|------|
| `assistant_message` | `AssistantMessage` | 触发工具调用的 assistant 消息 |
| `tool_call` | `AgentToolCall` | 当前工具调用 |
| `args` | `Any` | 已验证的参数 |
| `result` | `AgentToolResult[Any]` | 工具原始结果 |
| `is_error` | `bool` | 是否执行出错 |
| `context` | `AgentContext` | 当前上下文 |

### `AfterToolCallResult`

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `Optional[List[TextContent \| ImageContent]]` | 覆盖返回给 LLM 的内容 |
| `details` | `Optional[Any]` | 覆盖 details |
| `is_error` | `Optional[bool]` | 覆盖错误标记 |
| `terminate` | `Optional[bool]` | 覆盖 terminate 标记 |

### `ShouldStopAfterTurnContext`

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | `AssistantMessage` | 当前 turn 的 assistant 消息 |
| `tool_results` | `List[ToolResultMessage]` | 本 turn 产生的工具结果 |
| `context` | `AgentContext` | 当前上下文 |
| `new_messages` | `List[AgentMessage]` | 本 turn 新增的全部消息 |

### `PrepareNextTurnContext`

继承 `ShouldStopAfterTurnContext`，字段完全相同。

### `AgentLoopTurnUpdate`

| 字段 | 类型 | 说明 |
|------|------|------|
| `context` | `Optional[AgentContext]` | 替换下一轮使用的上下文 |
| `model` | `Optional[Model]` | 替换下一轮使用的模型 |
| `thinking_level` | `Optional[ModelThinkingLevel]` | 替换下一轮思考级别 |

---

## 事件类型

所有事件都继承 `NovaBaseModel`，`AgentEvent` 是它们的联合类型。

| 事件 | 字段 | 说明 |
|------|------|------|
| `AgentStartEvent` (`agent_start`) | - | 一次 run 开始 |
| `AgentEndEvent` (`agent_end`) | `messages: Optional[List[AgentMessage]]` | 一次 run 结束，携带最终消息列表 |
| `TurnStartEvent` (`turn_start`) | - | 新一轮 LLM 调用开始 |
| `TurnEndEvent` (`turn_end`) | `message: Optional[AgentMessage]`、`tool_results: Optional[List[ToolResultMessage]]` | 一轮结束 |
| `MessageStartEvent` (`message_start`) | `message: Optional[AgentMessage]` | 消息开始生成 |
| `MessageUpdateEvent` (`message_update`) | `message: Optional[AgentMessage]`、`assistant_message_event: Optional[AssistantMessageEvent]` | assistant 消息流式更新 |
| `MessageEndEvent` (`message_end`) | `message: Optional[AgentMessage]` | 消息生成结束 |
| `ToolExecutionStartEvent` (`tool_execution_start`) | `tool_call_id`、`tool_name`、`args` | 工具开始执行 |
| `ToolExecutionUpdateEvent` (`tool_execution_update`) | `tool_call_id`、`tool_name`、`args`、`partial_result` | 工具执行过程中的进度更新 |
| `ToolExecutionEndEvent` (`tool_execution_end`) | `tool_call_id`、`tool_name`、`result`、`is_error` | 工具执行结束 |

---

## 工具验证函数

```python
from nova_agent import (
    validate_tool_call,
    validate_tool_arguments,
    set_validation_enabled,
    clear_validator_cache,
)
```

| 函数 | 签名 | 说明 |
|------|------|------|
| `validate_tool_call` | `(tools: List[Tool], tool_call: ToolCall) -> Any` | 按名称查找工具并验证参数，失败抛出 `ValueError` |
| `validate_tool_arguments` | `(tool: Tool, tool_call: ToolCall) -> Any` | 直接对指定工具验证参数 |
| `set_validation_enabled` | `(enabled: bool) -> None` | 全局启用/禁用参数验证 |
| `clear_validator_cache` | `() -> None` | 清空已编译的 JSON Schema 验证器缓存 |

---

## `AbortSignal`

极简中断信号，用于在 run 生命周期内通知取消。

| 方法/属性 | 说明 |
|-----------|------|
| `aborted`（只读属性） | 是否已被中断 |
| `set()` | 触发中断 |
| `clear()` / `reset()` | 清除中断状态 |
| `is_set()` | 兼容 `asyncio.Event` 风格 |
| `__bool__` | 可直接用 `if signal:` 判断 |

---

## 类型别名

| 别名 | 定义 | 说明 |
|------|------|------|
| `AgentToolCall` | `ToolCall` | LLM 发出的单次工具调用 |
| `AgentEventSink` | `Callable[[AgentEvent], Awaitable[None]]` | loop 内部使用的事件下沉函数 |
| `AgentToolUpdateCallback` | `Callable[[AgentToolResult[Any]], None]` | 工具流式更新回调 |
| `StreamFn` | Protocol | 可同步或异步返回 stream 的函数签名 |
| `ToolExecutionMode` | `Literal["parallel", "sequential"]` | 工具执行策略 |
| `QueueMode` | `Literal["all", "one-at-a-time"]` | 队列 drain 策略 |
| `ModelThinkingLevel` | 来自 `nova_ai` | 思考级别：`"low"` / `"medium"` / `"high"` 等，依模型而定 |

---

## 内部工具执行类型（高级）

这些类型主要在 loop 与自定义工具实现之间使用，也可按需导入：

| 类型 | 说明 |
|------|------|
| `ExecutedToolCallOutcome` | 工具刚执行完产生的原始结果：`result` + `is_error` |
| `FinalizedToolCallOutcome` | 经过 `after_tool_call` Hook 处理后的最终结果 |
| `ExecutedToolCallBatch` | 单次 assistant 消息产生的所有 `ToolResultMessage` 及是否 `terminate` |
| `PreparedToolCall` | `_PreparedToolCallModel \| _ImmediateToolCallOutcome` 的联合类型 |

---

## 事件与 Hook 调用顺序

一次典型 turn 的调用顺序如下：

1. `agent_start`（仅在 run 开始时一次）
2. `turn_start`
3. `message_start` → 若干 `message_update` → `message_end`
4. 若 assistant 消息包含 tool calls：
   - 对每个 tool call 调用 `before_tool_call`
   - `tool_execution_start`
   - 执行工具 → 可能产生 `tool_execution_update`
   - 调用 `after_tool_call`
   - `tool_execution_end`
5. `turn_end`
6. `should_stop_after_turn` / `prepare_next_turn`
7. 重复 2–6，直到无更多 tool calls 或 `should_stop_after_turn` 返回 `True`
8. `agent_end`
