# API 参考

本文档提供 `nova_ai` 公共 API 的速查表。

---

## 调用入口

### `stream()`

```python
def stream(
    model: Model,
    context: Context,
    options: Optional[ProviderStreamOptions] = None,
) -> AssistantMessageEventStream
```

流式调用模型，返回异步可迭代的事件流。

**参数**：
- `model` —— 模型定义
- `context` —— 上下文（系统提示词、消息历史、工具定义）
- `options` —— 流式选项（`StreamOptions` 或协议特定选项）

**返回**：`AssistantMessageEventStream`，可 `async for` 迭代

**示例**：
```python
event_stream = stream(model, context)
async for event in event_stream:
    if event.type == "text_delta":
        print(event.delta, end="")
```

---

### `complete()`

```python
async def complete(
    model: Model,
    context: Context,
    options: Optional[ProviderStreamOptions] = None,
) -> AssistantMessage
```

非流式调用，内部调用 `stream()` 并聚合所有事件为完整消息。

**返回**：`AssistantMessage`，包含完整回复内容、用量统计和停止原因

**示例**：
```python
response = await complete(model, context)
print(response.content[0].text)
```

---

### `stream_simple()` / `complete_simple()`

简化版本，使用 `SimpleStreamOptions`（支持 `reasoning` 字段）：

```python
def stream_simple(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None,
) -> AssistantMessageEventStream

async def complete_simple(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None,
) -> AssistantMessage
```

---

## 核心类型

### Model

```python
@dataclass
class Model:
    id: str
    name: str
    api: str
    provider: str
    base_url: str
    max_tokens: int
    context_window: int
    input_types: List[str]
    cost: ModelCost
    reasoning: bool = False
    compat: Optional[OpenAICompletionsCompat] = None
    thinking_level_map: Optional[Dict[str, str]] = None
    headers: Optional[Dict[str, str]] = None
```

---

### Context

```python
@dataclass
class Context:
    system_prompt: Optional[str] = None
    messages: List[Message] = field(default_factory=list)
    tools: Optional[List[Tool]] = None
```

---

### Message 联合类型

```python
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]
```

#### UserMessage

```python
@dataclass
class UserMessage:
    role: Literal["user"] = "user"
    content: Union[str, List[Union[TextContent, ImageContent]]]
    timestamp: int = 0
```

#### AssistantMessage

```python
@dataclass
class AssistantMessage:
    role: Literal["assistant"] = "assistant"
    content: List[Union[TextContent, ThinkingContent, ToolCall]]
    api: Api = ""
    provider: Provider = ""
    model: str = ""
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.STOP
    error_message: Optional[str] = None
    response_id: Optional[str] = None
    response_model: Optional[str] = None
    timestamp: int = 0
```

#### ToolResultMessage

```python
@dataclass
class ToolResultMessage:
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: List[Union[TextContent, ImageContent]]
    details: Optional[Dict[str, Any]] = None
    is_error: bool = False
    timestamp: int = 0
```

---

### 内容块

#### TextContent

```python
@dataclass
class TextContent:
    type: Literal["text"] = "text"
    text: str = ""
```

#### ThinkingContent

```python
@dataclass
class ThinkingContent:
    type: Literal["thinking"] = "thinking"
    thinking: str = ""
    thinking_signature: Optional[str] = None
    redacted: bool = False
```

#### ToolCall

```python
@dataclass
class ToolCall:
    type: Literal["toolCall"] = "toolCall"
    id: str = ""
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    thought_signature: Optional[str] = None
```

#### ImageContent

```python
@dataclass
class ImageContent:
    type: Literal["image"] = "image"
    mime_type: str = ""
    data: str = ""  # base64
```

---

### 用量统计

```python
@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: Cost = field(default_factory=Cost)

@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0
```

---

## 事件类型

所有事件都包含 `partial: AssistantMessage` 字段，表示事件触发时的消息快照。

| 事件类型 | 字段 | 说明 |
|---------|------|------|
| `start` | — | 流开始 |
| `text_start` | `content_index` | 文本块开始 |
| `text_delta` | `content_index`, `delta` | 文本增量 |
| `text_end` | `content_index`, `content` | 文本块结束 |
| `thinking_start` | `content_index` | 思考块开始 |
| `thinking_delta` | `content_index`, `delta` | 思考增量 |
| `thinking_end` | `content_index`, `content` | 思考块结束 |
| `toolcall_start` | `content_index` | 工具调用开始 |
| `toolcall_delta` | `content_index`, `delta` | 工具参数增量（JSON 片段） |
| `toolcall_end` | `content_index`, `tool_call` | 工具调用结束，参数已解析为 dict |
| `done` | `reason`, `message` | 流正常完成 |
| `error` | `reason`, `error` | 流异常终止 |

---

## 注册表 API

### API Adapter 注册表

```python
# 注册（adapter 是实现 ApiAdapter Protocol 的对象）
class MyAdapter:
    api = "my-protocol"
    def stream(self, model, context, options=None): ...
    def stream_simple(self, model, context, options=None): ...

register_api_adapter(MyAdapter())

# 查询
adapter = get_api_adapter("my-protocol")  # None if not found
# 调用
adapter.stream(model, context, options)

# 列出
apis = list_api_adapters()  # List[str]

# 检查
has_api_adapter("my-protocol")  # bool

# 注销
unregister_api_adapter("my-protocol")

# 清空
clear_api_adapters()
```

### 模型注册表

```python
# 注册单个模型
register_model("provider", model)

# 批量注册
register_models_from_dict("provider", {"model-id": model})

# 查询
model = get_model("provider", "model-id")
model = get_model_by_id("model-id")  # 跨 provider 查找

# 列出
providers = list_providers()  # List[str]
all_models = list_all_models()  # Dict[str, Dict[str, Model]]

# 移除
remove_model("provider", "model-id")
remove_provider("provider")

# 清空
clear_model_registry()
```

---

## 工具函数

### 环境变量

```python
get_env_api_key("openai")           # Optional[str]
get_env_api_key_typed(KnownProvider.OPENAI)  # Optional[str]
get_all_env_api_keys()              # Dict[str, Optional[str]]
```

### 成本计算

```python
calculate_cost(model, usage)        # 返回 Cost，直接修改 usage.cost
supports_xhigh_thinking(model)      # bool
get_supported_thinking_levels(model)  # List[str]
```

### 溢出检测

```python
is_context_overflow(message, context_window=128000)  # bool
```

### 消息转换

```python
transform_messages(messages, model, normalize_tool_call_id=None)  # List[Message]
```

### JSON 解析

```python
parse_streaming_json(json_str)      # Dict or List，失败返回 {}
```

### 字符串清理

```python
sanitize_surrogates(text)           # 移除未配对 Unicode 代理项
```

---

## 流式选项

### StreamOptions

```python
class StreamOptions(NovaBaseModel):
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    api_key: Optional[str] = None
    transport: Optional[Transport] = None
    cache_retention: Optional[CacheRetention] = None
    session_id: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    metadata: Optional[Dict[str, Any]] = None
    timeout: Optional[float] = None
    max_retries: Optional[int] = None

    signal: Optional[Any] = Field(default=None, exclude=True)
    on_payload: Optional[Callable] = Field(default=None, exclude=True)
    on_response: Optional[Callable[[ProviderResponse, Any], None]] = Field(
        default=None, exclude=True
    )
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `temperature` | `float` | 采样温度（0–2） |
| `max_tokens` | `int` | 最大输出 token 数 |
| `api_key` | `str` | 显式指定 API key，覆盖环境变量 |
| `transport` | `Transport` | 传输层配置 |
| `cache_retention` | `CacheRetention` | 缓存保留策略：`SHORT` / `LONG` / `NONE` |
| `session_id` | `str` | 会话标识，用于缓存键 |
| `headers` | `Dict[str, str]` | 额外 HTTP 请求头 |
| `metadata` | `Dict[str, Any]` | 请求元数据，部分 provider 会记录 |
| `timeout` | `float` | 单次请求超时（秒），透传给 OpenAI SDK |
| `max_retries` | `int` | SDK 自动重试次数 |
| `signal` | `AbortSignal` | 取消信号（不参与序列化） |
| `on_payload` | `Callable` | 请求参数钩子（不参与序列化） |
| `on_response` | `Callable[[ProviderResponse, Model], None]` | 原始 HTTP 响应钩子（不参与序列化） |

### SimpleStreamOptions

```python
class SimpleStreamOptions(StreamOptions):
    reasoning: Optional[ThinkingLevel] = None
    thinking_budgets: Optional[ThinkingBudgets] = None
```

在 `StreamOptions` 基础上增加 `reasoning` 和 `thinking_budgets`，用于简化推理级别配置。

### ProviderResponse

```python
class ProviderResponse(NovaBaseModel):
    status: int
    headers: Dict[str, str]
```

`on_response` 回调接收的类型，包含底层 HTTP 响应的状态码和响应头。

---

## 枚举

```python
class KnownApi(Enum):
    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    # ...

class KnownProvider(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    VOLCENGINE = "volcengine"
    # ...

class StopReason(Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_USE = "tool_use"
    ERROR = "error"
    ABORTED = "aborted"

class ThinkingLevel(Enum):
    OFF = "off"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
```
