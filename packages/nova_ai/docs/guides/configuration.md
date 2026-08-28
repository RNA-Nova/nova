# 配置指南

本文档说明 `nova_ai` 的配置方式，包括环境变量、模型配置和流式选项。

---

## 环境变量

### API Key

`nova_ai` 通过 `utils/env.py` 从环境变量自动读取各提供商的 API key：

| 提供商 | 环境变量 |
|--------|---------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` |
| GitHub Copilot | `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` |
| Google (Gemini) | `GEMINI_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Volcengine | `VOLCENGINE_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Mistral | `MISTRAL_API_KEY` |
| xAI | `XAI_API_KEY` |
| ... | 详见 `utils/env.py` |

```bash
export OPENAI_API_KEY="sk-..."
export VOLCENGINE_API_KEY="..."
```

### 缓存保留策略

```bash
export PI_CACHE_RETENTION="long"   # long | short | none
```

影响 OpenAI 提示缓存的行为。详见 `api_impls/openai_completions.py` 的 `_resolve_cache_retention()`。

---

## 模型配置

### 手动构造 Model

```python
from nova_ai import Model, ModelCost, KnownApi, KnownProvider

model = Model(
    id="gpt-4o",
    name="GPT-4o",
    api=KnownApi.OPENAI_COMPLETIONS,
    provider=KnownProvider.OPENAI,
    base_url="https://api.openai.com/v1/",
    max_tokens=4096,
    context_window=128000,
    input_types=["text", "image"],
    cost=ModelCost(input=5.0, output=15.0, cache_read=0.0, cache_write=0.0),
    reasoning=False,
)
```

### 使用内置模型

```python
from nova_ai import VOLCENGINE_MODELS, get_volcengine_model

model = get_volcengine_model("deepseek-v3-2-251201")
```

### 模型字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 模型唯一标识，用于 API 请求 |
| `name` | `str` | 展示名称 |
| `api` | `str` / `KnownApi` | 使用的 API 协议 |
| `provider` | `str` / `KnownProvider` | 服务提供商 |
| `base_url` | `str` | API 基础 URL |
| `max_tokens` | `int` | 最大输出 token 数 |
| `context_window` | `int` | 上下文窗口大小（输入 + 输出总上限） |
| `input_types` | `List[str]` | 支持的输入类型：`["text"]`, `["text", "image"]` |
| `cost` | `ModelCost` | 成本定义（$/M tokens） |
| `reasoning` | `bool` | 是否支持推理/思考模式 |
| `compat` | `OpenAICompletionsCompat` | 兼容性配置覆盖 |
| `thinking_level_map` | `Dict[str, str]` | 思考级别映射 |
| `headers` | `Dict[str, str]` | 额外请求头 |

---

## 流式选项

### StreamOptions

```python
from nova_ai import StreamOptions

options = StreamOptions(
    temperature=0.7,           # 采样温度（0-2）
    max_tokens=2048,           # 最大输出 token 数
    api_key="sk-...",          # 显式指定 API key
    headers={"X-Custom": "v"}, # 自定义请求头
    session_id="sess-123",     # 会话 ID（用于缓存）
    cache_retention="long",    # 缓存保留策略
    timeout=60.0,              # 请求超时（秒）
    max_retries=2,             # 最大重试次数
    metadata={"key": "value"}, # 请求元数据（provider 透传）
)
```

### SimpleStreamOptions

```python
from nova_ai import SimpleStreamOptions, ThinkingLevel

options = SimpleStreamOptions(
    temperature=0.7,
    max_tokens=2048,
    reasoning=ThinkingLevel.HIGH,  # 推理级别
    tool_choice="auto",              # 工具选择策略
)
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `temperature` | `float` | 采样温度，默认 1.0 |
| `max_tokens` | `int` | 最大输出 token 数 |
| `reasoning` | `ThinkingLevel` | 推理级别：`MINIMAL`, `LOW`, `MEDIUM`, `HIGH`, `XHIGH`；不传（`None`）表示关闭思考 |
| `thinking_budgets` | `ThinkingBudgets` | 各级别的 token 预算 |
| `api_key` | `str` | 显式 API key（覆盖环境变量） |
| `transport` | `Transport` | 传输层配置 |
| `cache_retention` | `CacheRetention` | 缓存保留策略：`SHORT`, `LONG`, `NONE` |
| `session_id` | `str` | 会话标识（用于缓存键） |
| `headers` | `Dict[str, str]` | 额外 HTTP 请求头 |
| `tool_choice` | `str` / `Dict` | 工具选择策略：`auto`, `none`, `required`, 或指定工具 |
| `timeout` | `float` | 请求超时（秒），透传给 OpenAI SDK |
| `max_retries` | `int` | 最大重试次数，透传给 OpenAI SDK |
| `metadata` | `Dict[str, Any]` | 请求元数据，部分 provider 会记录到后端 |
| `headers` | `Dict[str, str]` | 额外 HTTP 请求头 |
| `on_payload` | `Callable` | 请求参数钩子，在发送前调用 |
| `on_response` | `Callable[[ProviderResponse, Model], None]` | 原始 HTTP 响应钩子，可拿到状态码和响应头 |
| `signal` | `AbortSignal` | 取消信号，触发后主动关闭底层流 |

---

## 兼容性配置

对于通过 OpenAI Completions API 接入的第三方服务，可以覆盖自动检测的兼容性设置：

```python
from nova_ai import OpenAICompletionsCompat, ThinkingFormat

model = Model(
    # ...其他字段...
    compat=OpenAICompletionsCompat(
        supports_store=False,                           # 不支持 store 参数
        supports_developer_role=False,                  # 不支持 developer 角色
        max_tokens_field="max_tokens",                  # 使用 max_tokens 而非 max_completion_tokens
        thinking_format=ThinkingFormat.DEEPSEEK,        # thinking 参数格式
        requires_reasoning_content_on_assistant_messages=True,
    ),
)
```

`compat` 中的非 None 字段会覆盖 `detect_compat()` 的自动检测结果。如果留 None，则使用自动检测结果。

---

## 思考级别映射

模型支持 reasoning 时，可以通过 `thinking_level_map` 映射标准思考级别到提供商特定的值：

```python
model = Model(
    # ...
    reasoning=True,
    thinking_level_map={
        "off": "disabled",
        "minimal": None,       # None 表示该级别不支持
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "max",        # 显式定义才支持 xhigh
    },
)
```

标准思考级别：`off` → `minimal` → `low` → `medium` → `high` → `xhigh`

---

## 注册自定义模型

在运行时添加自定义模型：

```python
from nova_ai import register_model

register_model("my-provider", my_custom_model)

# 查询
from nova_ai import get_model
model = get_model("my-provider", "my-model-id")
```

自定义模型只在当前进程有效，进程重启后需要重新注册。
