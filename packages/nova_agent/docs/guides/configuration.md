# 配置指南

本文档说明 `nova_agent` 的常用配置项。

---

## 模型配置

```python
from nova_ai import get_volcengine_model

model = get_volcengine_model("deepseek-v4-flash-260425")
agent = Agent()
agent.set_model(model)
```

## 手动构造 Model

如果你不用内置模型工厂，可以手动构造：

```python
from nova_ai import Model, ModelCost, KnownApi, KnownProvider

model = Model(
    id="deepseek-v4-flash-260425",
    name="DeepSeek V4 Flash",
    api=KnownApi.OPENAI_COMPLETIONS,      # 使用 OpenAI-compatible 协议
    provider=KnownProvider.VOLCENGINE,    # 服务提供商
    base_url="https://ark.cn-beijing.volces.com/api/v3/",
    max_tokens=4096,
    context_window=131072,
    input_types=["text"],
    cost=ModelCost(input=2.0, output=8.0, cache_read=0.0, cache_write=0.0),
    reasoning=True,
)
```

### `api` 字段说明

`api` 决定 `nova_ai` 使用哪个协议实现去调用模型：

| `KnownApi` | 说明 |
|---|---|
| `OPENAI_COMPLETIONS` | OpenAI Chat Completions 兼容协议（最常用） |
| `OPENAI_RESPONSES` | OpenAI Responses API |
| `ANTHROPIC_MESSAGES` | Anthropic Messages API |
| `GOOGLE_GENERATIVE_LANGUAGE` | Google Gemini API |
| `BEDROCK` | AWS Bedrock |
| `VERTEX` | Google Vertex AI |

`provider` 用于环境变量解析、成本计算和兼容性检测。常见值：

```python
KnownProvider.OPENAI
KnownProvider.ANTHROPIC
KnownProvider.VOLCENGINE
KnownProvider.DEEPSEEK
KnownProvider.GOOGLE
```

## 工具执行模式

```python
# 全局 sequential
agent = Agent(tool_execution="sequential")

# 或在工具上单独覆盖
class SlowTool(AgentTool):
    execution_mode: str = "sequential"
```

## 队列模式

```python
agent = Agent(
    steering_mode="one-at-a-time",   # 默认
    follow_up_mode="all",            # 一次 drain 所有 follow_up
)
```

## 动态 API Key

用于短效 token 场景：

```python
async def get_api_key(provider: str):
    return await refresh_token(provider)

agent = Agent(get_api_key=get_api_key)
```

## 会话与缓存

```python
agent = Agent(session_id="session-123")
```

`session_id` 会透传给支持缓存的 provider，用于提示缓存亲和性。

## Reasoning / Thinking

```python
agent.set_thinking_level("medium")
```

thinking level 会透传给 provider，具体支持取决于模型。

## 常用配置速查

| 配置 | 类型 | 说明 |
|------|------|------|
| `model` | `Model` | 使用的 LLM |
| `tools` | `List[AgentTool]` | 可用工具 |
| `system_prompt` | `str` | 系统提示词 |
| `tool_execution` | `"parallel"` / `"sequential"` | 工具执行模式 |
| `steering_mode` | `"one-at-a-time"` / `"all"` | steering 队列出队模式 |
| `follow_up_mode` | `"one-at-a-time"` / `"all"` | follow_up 队列出队模式 |
| `session_id` | `str` | 缓存会话 ID |
| `max_retry_delay_ms` | `int` | provider 请求最大重试等待 |
