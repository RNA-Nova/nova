# 错误处理指南

本文档说明 `nova_agent` 中的错误类型、检测方法和处理策略。

---

## 错误类型总览

| 错误场景 | 检测方式 | 处理建议 |
|---------|---------|---------|
| 工具执行失败 | `tool_execution_end` 的 `is_error=True` | 查看 `result.content`，模型会在下一轮看到 error toolResult |
| 工具未找到 | `tool_execution_end` 的 `is_error=True` | 检查 tool name 是否已注册 |
| 参数校验失败 | `tool_execution_end` 的 `is_error=True` | 检查 schema 和模型生成的参数 |
| Provider 请求失败 | `agent_end` 前出现 `message_end`（error_message 非空） | 检查 `state.error_message` |
| 请求取消 | `AbortSignal` 被设置 | 正常终止，无需重试 |

---

## 工具错误

工具执行抛出异常时，框架会自动生成 error toolResult：

```python
async def execute(self, tool_call_id, params, signal=None, on_update=None):
    raise RuntimeError("something went wrong")
```

对应的事件：

```python
{
    "type": "tool_execution_end",
    "tool_call_id": "...",
    "tool_name": "...",
    "result": AgentToolResult(content=[TextContent(text="something went wrong")]),
    "is_error": True,
}
```

模型会在下一轮看到这条 `toolResult`，并自行决定如何处理。

---

## 请求取消

通过 `Agent.abort()` 触发：

```python
agent.abort()
```

取消后：

- 底层 provider stream 会被关闭。
- 正在执行的工具如果检查 `signal.aborted`，应抛出异常或提前返回。
- loop 会尝试再生成一次 assistant 响应（通常为空），然后 `agent_end`。
- 最终 `state.messages[-1].role == "assistant"`。

---

## Provider 失败

如果 provider 返回错误，`agent_loop` 会抛出异常，由 `Agent._handle_run_failure` 处理：

- 生成一条 failure assistant 消息。
- 推送 `message_start` / `message_end` / `turn_end` / `agent_end`。
- 可通过 `state.error_message` 查看错误信息。

当前没有 `on_run_error` hook，失败后的行为是框架写死的。如需自定义，可在 `Agent` 外层 try/except 捕获异常。

---

## 建议

1. 工具内部用异常表示失败，不要用 `AgentToolResult(is_error=True)` 包装业务错误。
2. `before_tool_call` 可用于权限校验，阻止危险工具执行。
3. 长时间运行的工具应定期检查 `signal.aborted`。
