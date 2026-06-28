# 错误处理指南

本文档说明 `nova_ai` 中的错误类型、检测方法和处理策略。

---

## 错误类型总览

| 错误场景 | 检测方式 | 处理建议 |
|---------|---------|---------|
| 网络/API 错误 | `ErrorEvent` + `StopReason.ERROR` | 检查 `error_message`，考虑重试 |
| 上下文溢出 | `is_context_overflow()` | 压缩上下文、换用更大窗口的模型 |
| 请求取消 | `AbortSignal` 或 task cancel | 正常终止，无需重试 |
| 长度限制 | `StopReason.LENGTH` | 增加 `max_tokens` 或分段处理 |
| 内容过滤 | `StopReason.ERROR` + 特定错误信息 | 调整输入内容 |

---

## 流式调用中的错误

流式调用不会直接抛异常，而是通过 `ErrorEvent` 推送错误：

```python
from nova_ai import StopReason

async for event in event_stream:
    if event.type == "error":
        error_msg = event.error.error_message
        print(f"错误: {error_msg}")
        print(f"停止原因: {event.reason}")
        
        # event.error 是 AssistantMessage，包含完整的错误状态
        print(f"Usage: {event.error.usage}")
        break
```

**注意**：`ErrorEvent` 的 `error` 字段是一个 `AssistantMessage` 对象，不是 `Exception`。这让你能获取到错误发生时的完整状态（token 用量、已生成的内容等）。

---

## 非流式调用中的错误

`complete()` 和 `complete_simple()` 不会直接抛异常。如果流内部发生错误，它们会返回一个 `stop_reason=ERROR` 的 `AssistantMessage`：

```python
response = await complete(model, context)

if response.stop_reason == StopReason.ERROR:
    print(f"请求失败: {response.error_message}")
    # response 中可能包含部分生成的内容
    if response.content:
        print(f"部分结果: {response.content[0].text}")
elif response.stop_reason == StopReason.LENGTH:
    print("输出被截断，需要增加 max_tokens")
else:
    print(f"成功: {response.content[0].text}")
```

---

## 上下文溢出检测

使用 `is_context_overflow()` 检测上下文窗口溢出：

```python
from nova_ai import is_context_overflow

response = await complete(model, context)

if is_context_overflow(response, context_window=model.context_window):
    print("上下文溢出！需要压缩或截断历史消息")
    
    # 策略 1：截断早期消息
    context.messages = context.messages[-10:]
    
    # 策略 2：换用更大窗口的模型
    # model.context_window = 1047576  # 使用更大的模型
    
    # 重新请求
    response = await complete(model, context)
```

`is_context_overflow()` 支持两种检测方式：

1. **基于错误消息模式匹配** —— 检查 `error_message` 是否包含已知提供商的溢出关键词
2. **基于 token 数量** —— 如果传了 `context_window` 参数，检查 `usage.input > context_window`

支持的提供商包括：OpenAI、Anthropic、Google、xAI、Groq、OpenRouter、DeepSeek、Mistral、Cerebras、Volcengine 等。

---

## 取消请求

### 使用 AbortSignal

```python
from nova_ai import AbortSignal

signal = AbortSignal()
options = SimpleStreamOptions(signal=signal)

# 取消信号
signal.abort()

# OpenAI Completions 实现会主动关闭底层 HTTP 流，
# 已收到的 content block 仍会正常收尾，最终推送 ErrorEvent(reason="aborted")
```

**取消后的事件序列**：

```
start
text_delta / thinking_delta / toolcall_delta ...（已收到的增量）
text_end / thinking_end / toolcall_end ...（已收到 block 的收尾）
ErrorEvent(reason="aborted")
stream.end()
```

**注意**：`AbortSignal` 触发后会立即关闭底层连接，但已经在内存中的内容块会正常结束，事件流不会错乱。

### 使用 asyncio.Task.cancel()

```python
task = asyncio.create_task(complete(model, context))
task.cancel()

try:
    response = await task
except asyncio.CancelledError:
    print("请求被取消")
```

**注意**：`CancelledError` 会被 `EventStream` 正确处理，不会导致资源泄漏。

---

## 重试策略

`nova_ai` 本身不提供内置重试机制，但可以通过 `on_payload` / `on_response` 钩子观察请求参数和原始响应：

```python
def log_payload(params):
    print(f"请求参数: {params}")

def log_response(response, model):
    print(f"HTTP {response.status}")
    print(f"响应头: {response.headers}")

options = SimpleStreamOptions(
    on_payload=log_payload,
    on_response=log_response,
)
```

如果需要重试，建议在调用层实现：

```python
import asyncio

async def complete_with_retry(model, context, max_retries=3):
    for i in range(max_retries):
        try:
            response = await complete(model, context)
            if response.stop_reason != StopReason.ERROR:
                return response
            
            if i < max_retries - 1:
                wait = 2 ** i  # 指数退避
                print(f"请求失败，{wait}s 后重试...")
                await asyncio.sleep(wait)
        except Exception as e:
            if i == max_retries - 1:
                raise
    
    return response
```

---

## 常见错误信息对照

| 错误关键词 | 提供商 | 含义 |
|-----------|--------|------|
| `exceeds the context window` | OpenAI | 输入超过上下文窗口 |
| `prompt is too long` | Anthropic | 提示词太长 |
| `input token count exceeds` | Google | 输入 token 超限 |
| `maximum prompt length` | xAI | 提示长度超限 |
| `context window exceeds limit` | Volcengine/DeepSeek | 上下文窗口超限 |
| `reduce the length` | Groq | 消息长度需要减少 |
| `400/413 status code (no body)` | Cerebras/Mistral | 上下文溢出（无响应体） |

更多模式见 `utils/overflow.py` 中的 `OVERFLOW_PATTERNS`。
