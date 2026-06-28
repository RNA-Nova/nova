# 快速上手

5 分钟学会使用 `nova_ai` 发起第一个 LLM 请求。

---

## 安装

```bash
pip install -e packages/nova_ai
```

依赖：`openai`、`json-repair`、`mashumaro`。

---

## 第一个请求

```python
import asyncio
from nova_ai import (
    Model, ModelCost, Context, UserMessage,
    stream, complete,
    KnownProvider, KnownApi,
)

# 定义模型
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

# 构建上下文
context = Context(
    system_prompt="你是一个 helpful assistant。",
    messages=[
        UserMessage(content="你好，介绍一下自己。"),
    ],
)

# 非流式调用
async def main():
    response = await complete(model, context)
    print(response.content[0].text)

asyncio.run(main())
```

---

## 环境变量配置 API Key

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

`nova_ai` 会自动从环境变量读取对应 provider 的 API key。支持的环境变量映射见 `utils/env.py`。

如果不设置环境变量，也可以在调用时显式传入：

```python
from nova_ai import SimpleStreamOptions

options = SimpleStreamOptions(api_key="your-api-key")
response = await complete(model, context, options)
```

---

## 流式调用

```python
from nova_ai import stream

async def main():
    event_stream = stream(model, context)
    
    async for event in event_stream:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)
        elif event.type == "done":
            print("\n[完成]")
            print(f"输入 token: {event.message.usage.input}")
            print(f"输出 token: {event.message.usage.output}")
            print(f"成本: ${event.message.usage.cost.total:.6f}")

asyncio.run(main())
```

---

## 使用内置模型

如果不想手动构造 `Model`，可以使用内置的模型数据：

```python
from nova_ai import VOLCENGINE_MODELS, get_volcengine_model

# 获取内置模型
model = get_volcengine_model("deepseek-v3-2-251201")

# 或列出所有内置模型
for model_id, model in VOLCENGINE_MODELS.items():
    print(f"{model_id}: {model.name}")
```

---

## 下一步

- 查看 [examples.md](./examples.md) 了解工具调用、图片输入、推理模式等高级用法
- 查看 [api-reference.md](../reference/api-reference.md) 了解完整的 API 接口
- 查看 [configuration.md](./configuration.md) 了解环境变量和配置选项
