# Nova Agent 架构设计

## 1. 定位

`nova_agent` 是 Nova 单体仓库中的**事件驱动异步 Agent 框架**。它基于 `nova_ai` 提供的多厂商 LLM 能力，向上层（如 `nova_harness`）提供 Agent 运行时、状态管理、工具执行和生命周期控制。

```
nova_harness (高阶 SDK)
    ↑
nova_agent ← 本包（Agent 运行时）
    ↑
nova_ai    （LLM 提供商抽象层）
```

## 2. 模块划分

```
src/nova_agent/
├── agent.py              # Agent 包装类：状态、队列、生命周期、事件分发
├── agent_loop/           # Agent 循环核心
│   ├── loop.py           # 主循环、assistant 流式响应处理
│   ├── tools.py          # 工具调用执行（sequential / parallel）
│   └── facade.py         # EventStream 对外入口
├── types/                # 类型定义
│   ├── context.py        # AgentContext, AgentLoopConfig
│   ├── events.py         # AgentEvent 体系
│   ├── state.py          # AgentState
│   └── tool.py           # AgentTool, AgentToolResult
├── utils.py              # 工具参数 JSON Schema 校验
├── signal.py             # AbortSignal
└── __init__.py           # 公共 API 导出
```

## 3. 核心数据流

### 3.1 单次 prompt 流程

```
调用方
  │
  ▼
Agent.prompt(messages)
  │
  ▼
run_agent_loop(prompts, context, config, emit, signal, stream_fn)
  │
  ▼
_stream_assistant_response
  ├── transform_context（可选）
  ├── convert_to_llm（默认过滤 user/assistant/toolResult）
  ├── 调用 stream_fn / stream_simple
  └── 转发 provider 事件为 agent 事件
  │
  ▼
execute_tool_calls（如有 tool calls）
  ├── before_tool_call hook
  ├── 执行工具（sequential / parallel）
  ├── after_tool_call hook
  └── 生成 ToolResultMessage
  │
  ▼
prepare_next_turn / should_stop_after_turn
  │
  ▼
下一轮 或 agent_end
```

### 3.2 事件流

```text
agent_start
  turn_start
    message_start (user)
    message_end   (user)
    message_start (assistant)
    message_update × N
    message_end   (assistant)
    tool_execution_start
    tool_execution_update（可选）
    tool_execution_end
    message_start (toolResult)
    message_end   (toolResult)
  turn_end
agent_end
```

## 4. 关键设计决策

### 4.1 消息类型统一在 Agent 层

`AgentMessage` 允许自定义消息类型。只有在进入 LLM 调用边界时，才通过 `convert_to_llm` 转成 LLM 能理解的消息。

### 4.2 事件流的两层拷贝

- `nova_ai` provider 层对 `output` 深拷贝，防止 EventStream 缓冲时事件被后续 mutate 覆盖。
- `agent_loop` 对 `message_start` / `message_update` 浅拷贝，保证上层 listener 事件稳定。

### 4.3 工具终止语义

工具结果可设置 `terminate=True`，但仅当当前 batch **所有**工具结果都终止时，loop 才跳过自动 follow-up LLM 调用。避免单个工具意外中断多工具流程。

### 4.4 队列机制

- `steer()`：在当前 run 进行中注入用户消息，打断当前 agent。
- `follow_up()`：在当前 run 自然结束后注入用户消息，用于自动续跑。

## 5. 扩展点

主要 hook：

- `convert_to_llm` / `transform_context`：消息转换与上下文管理
- `before_tool_call` / `after_tool_call`：工具生命周期干预
- `prepare_next_turn`：动态调整下一轮 context/model/thinking
- `should_stop_after_turn`：优雅停止
- `get_api_key`：动态鉴权
- `on_payload` / `on_response`：观测原始请求/响应
