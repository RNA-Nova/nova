# Hook 机制

`nova_agent` 通过 hook 让使用者在关键节点插入自定义逻辑。

## 当前 hook 一览

| Hook | 触发时机 | 用途 |
|---|---|---|
| `convert_to_llm` | 每次调 LLM 前 | 把 `AgentMessage[]` 转成 LLM 可理解的消息 |
| `transform_context` | `convert_to_llm` 之前 | 上下文裁剪、注入外部信息 |
| `get_api_key` | 每次调 LLM 前 | 动态刷新 OAuth / 短效 token |
| `on_payload` / `on_response` | provider 请求前后 | 观测原始请求和响应 |
| `before_tool_call` | 参数校验通过后、执行前 | 拦截/阻断工具调用 |
| `after_tool_call` | 工具执行完成后 | 覆盖 tool result 的 content/details/is_error/terminate |
| `prepare_next_turn` | turn 结束后 | 修改下一轮 context/model/thinking |
| `should_stop_after_turn` | turn 正常结束后 | 决定是否优雅退出 |

## convert_to_llm

默认只保留 `user` / `assistant` / `toolResult` 消息。如果你的应用自定义了消息类型，必须在这里转换或过滤。

```python
async def convert(messages):
    return [m for m in messages if m.role in ("user", "assistant", "toolResult")]

agent = Agent(convert_to_llm=convert)
```

## transform_context

适合做上下文窗口管理：

```python
async def transform(messages, signal):
    if estimate_tokens(messages) > MAX_TOKENS:
        return prune_old_messages(messages)
    return messages
```

## before_tool_call

返回 `block=True` 可阻止工具执行，loop 会生成 error toolResult。

```python
async def before(ctx, signal):
    if signal and signal.aborted:
        return None
    if ctx.tool_call.name == "bash":
        return {"block": True, "reason": "bash 未授权"}
```

## after_tool_call

可以覆盖结果的任意字段， omitted 字段保持原值。

```python
async def after(ctx, signal):
    if ctx.tool_call.name == "notify_done":
        return {"terminate": True}
    return None
```

## prepare_next_turn

返回 `AgentLoopTurnUpdate` 可动态切换模型或调整上下文：

```python
from nova_ai import ModelThinkingLevel

async def prepare(ctx):
    return {
        "context": ctx.context,
        "model": cheaper_model,
        "thinking_level": ModelThinkingLevel.OFF,
    }
```

## should_stop_after_turn

用于在 context 过长前主动停止：

```python
async def should_stop(ctx):
    return estimate_tokens(ctx.context.messages) > LIMIT
```

## 还缺少的 hook

- **错误处理 hook**：`on_run_error` / `on_tool_error`，目前失败后的 assistant failure 消息是框架写死的。
- **重试 hook**：provider 或工具出错时没有统一的重试决策点。
