# 使用示例

本文档展示 `nova_ai` 的常见使用场景。

---

## 目录

1. [基础流式调用](#基础流式调用)
2. [非流式调用](#非流式调用)
3. [工具调用](#工具调用)
4. [推理模式](#推理模式)
5. [图片输入](#图片输入)
6. [自定义请求头](#自定义请求头)
7. [取消请求](#取消请求)
8. [查看原始 HTTP 响应](#查看原始-http-响应)
9. [超时与重试](#超时与重试)
10. [查看用量和成本](#查看用量和成本)

---

## 基础流式调用

```python
import asyncio
from nova_ai import (
    Model, ModelCost, Context, UserMessage,
    stream, KnownApi, KnownProvider,
)

model = Model(
    id="deepseek-v3-2-251201",
    name="Deepseek-v3-2",
    api=KnownApi.OPENAI_COMPLETIONS,
    provider=KnownProvider.VOLCENGINE,
    base_url="https://ark.cn-beijing.volces.com/api/v3/",
    max_tokens=4096,
    context_window=131072,
    input_types=["text"],
    cost=ModelCost(input=2.0, output=8.0, cache_read=0.5, cache_write=0.0),
)

context = Context(
    messages=[UserMessage(content="讲一个关于程序员的笑话。")],
)

async def main():
    event_stream = stream(model, context)
    
    async for event in event_stream:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "done":
            print("\n---")
            msg = event.message
            print(f"Token: {msg.usage.input} → {msg.usage.output}")
            print(f"成本: ${msg.usage.cost.total:.6f}")

asyncio.run(main())
```

---

## 非流式调用

```python
from nova_ai import complete

async def main():
    response = await complete(model, context)
    print(response.content[0].text)
    print(f"Stop reason: {response.stop_reason}")

asyncio.run(main())
```

---

## 工具调用

```python
from nova_ai import Tool, ToolResultMessage, TextContent

# 定义工具
tools = [
    Tool(
        name="get_weather",
        description="获取指定城市的天气",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"},
            },
            "required": ["city"],
        },
    ),
]

context = Context(
    messages=[UserMessage(content="北京今天天气怎么样？")],
    tools=tools,
)

async def main():
    event_stream = stream(model, context)
    
    async for event in event_stream:
        if event.type == "toolcall_end":
            tc = event.tool_call
            print(f"工具调用: {tc.name}({tc.arguments})")
            
            # 执行工具并返回结果
            result = "北京今天晴，25°C"
            
            context.messages.append(ToolResultMessage(
                tool_call_id=tc.id,
                tool_name=tc.name,
                content=[TextContent(text=result)],
            ))
            
            # 继续对话
            continue_stream = stream(model, context)
            async for evt in continue_stream:
                if evt.type == "text_delta":
                    print(evt.delta, end="")

asyncio.run(main())
```

---

## 推理模式

启用模型的 reasoning/thinking 模式：

```python
from nova_ai import SimpleStreamOptions, ThinkingLevel

options = SimpleStreamOptions(
    reasoning=ThinkingLevel.HIGH,  # off | minimal | low | medium | high | xhigh
)

event_stream = stream(model, context, options)

async for event in event_stream:
    if event.type == "thinking_delta":
        print(f"[思考] {event.delta}", end="")
    elif event.type == "text_delta":
        print(event.delta, end="")
```

**注意**：
- 只有 `reasoning=True` 的模型支持推理模式
- `xhigh` 级别需要模型显式支持（通过 `supports_xhigh_thinking()` 检查）
- 模型不支持 reasoning 时，`ThinkingLevel` 会被忽略

---

## 图片输入

```python
from nova_ai import ImageContent

# 模型必须支持 image 输入类型
model = Model(
    # ...
    input_types=["text", "image"],
)

context = Context(
    messages=[
        UserMessage(content=[
            TextContent(text="描述这张图片"),
            ImageContent(
                mime_type="image/jpeg",
                data="base64encodedstring...",
            ),
        ]),
    ],
)
```

---

## 自定义请求头

```python
options = SimpleStreamOptions(
    headers={"X-Custom-Header": "value"},
)

event_stream = stream(model, context, options)
```

---

## 取消请求

```python
from nova_ai import AbortSignal

signal = AbortSignal()
options = SimpleStreamOptions(signal=signal)

# 1 秒后取消
async def cancel_later():
    await asyncio.sleep(1)
    signal.abort()

async def main():
    asyncio.create_task(cancel_later())

    event_stream = stream(model, context, options)

    async for event in event_stream:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "error":
            print(f"\n请求终止，原因: {event.reason}")
            print(f"错误信息: {event.error.error_message}")

asyncio.run(main())
```

**说明**：触发 `signal.abort()` 后，OpenAI Completions 实现会主动关闭底层 HTTP 流。已收到的内容块仍会正常收尾（推送 `*_end` 事件），最终通过 `ErrorEvent(reason="aborted")` 结束。

---

## 查看原始 HTTP 响应

通过 `on_response` 回调获取状态码和响应头：

```python
from nova_ai import ProviderResponse

def on_response(response: ProviderResponse, model):
    print(f"HTTP 状态码: {response.status}")
    print(f"响应头: {response.headers}")

options = SimpleStreamOptions(
    on_response=on_response,
)

async def main():
    event_stream = stream(model, context, options)
    async for event in event_stream:
        if event.type == "text_delta":
            print(event.delta, end="")

asyncio.run(main())
```

---

## 超时与重试

```python
options = SimpleStreamOptions(
    timeout=30.0,      # 单次请求最多等待 30 秒
    max_retries=0,     # 关闭 SDK 自动重试
)

async def main():
    event_stream = stream(model, context, options)
    async for event in event_stream:
        if event.type == "text_delta":
            print(event.delta, end="")

asyncio.run(main())
```

---

---

## 查看用量和成本

```python
async def main():
    response = await complete(model, context)
    usage = response.usage
    
    print(f"输入 token: {usage.input}")
    print(f"输出 token: {usage.output}")
    print(f"缓存读取: {usage.cache_read}")
    print(f"缓存写入: {usage.cache_write}")
    print(f"总 token: {usage.total_tokens}")
    
    cost = usage.cost
    print(f"输入成本: ${cost.input:.6f}")
    print(f"输出成本: ${cost.output:.6f}")
    print(f"总成本: ${cost.total:.6f}")

asyncio.run(main())
```
