# nova-ai

统一的多厂商 LLM 抽象层。以 **Models 集合 + Provider 运行时单元 + API 协议实现**三层组织，对外暴露一致的 `stream` / `complete` / `stream_simple` / `complete_simple` 异步 API。

## 特性

- **统一流式接口**：所有调用返回同一个 `AssistantMessageEventStream`，文本、思考、工具调用以增量事件产出；`stream()` 同步返回事件流，鉴权解析在后台进行，失败编码为错误事件而非抛出。
- **内置 4 家 provider**：Volcengine（火山方舟）、Moonshot AI（国际/国内双端点）、Kimi Coding，均走 OpenAI Completions 协议；任意 OpenAI 兼容端点可经 `create_provider()` 接入。
- **鉴权解析链**：调用方 `api_key` 覆盖 → 已存储 credential（OAuth 过期自动刷新）→ 环境变量；内置 Kimi device code 与 OpenAI Codex（浏览器 + device code）OAuth 登录流程。
- **完整流式事件体系**：`text_*` / `thinking_*` / `toolcall_*` 增量事件，工具参数带部分 JSON 增量解析快照。
- **思考级别统一抽象**：一套 `ThinkingLevel` 级别，按厂商参数格式自动分派（`reasoning_effort`、`thinking`、`reasoning` 对象等）。
- **token 用量与成本统计**：输入/输出/缓存读写分项计数，按模型费率（支持分层定价）实时算出成本。
- **兼容层**：按 provider / base_url 自动检测端点兼容性，`model.compat` 可逐字段显式覆盖。
- **工程化细节**：`AbortController` 取消（主动关闭底层 HTTP 流）、`on_payload` / `on_response` 调试钩子、可被取消打断的请求层重试、错误一律编码进事件流终态。

## 目录

- [支持的 Provider](#支持的-provider)
- [安装](#安装)
- [快速上手](#快速上手)
- [Provider 与模型](#provider-与模型)
- [鉴权](#鉴权)
- [工具调用](#工具调用)
- [图片输入](#图片输入)
- [Thinking / Reasoning](#thinking--reasoning)
- [停止原因](#停止原因)
- [错误处理与请求中止](#错误处理与请求中止)
- [自定义 Provider](#自定义-provider)
- [流式事件参考](#流式事件参考)
- [跨模型交接](#跨模型交接)
- [示例](#示例)
- [开发](#开发)
- [License](#license)

## 支持的 Provider

| Provider | id | 端点 | 鉴权 |
|----------|----|------|------|
| Volcengine（火山方舟） | `volcengine` | `https://ark.cn-beijing.volces.com/api/v3/` | `VOLCENGINE_API_KEY` |
| Moonshot AI（国际） | `moonshotai` | `https://api.moonshot.ai/v1` | `MOONSHOT_API_KEY` |
| Moonshot AI（国内） | `moonshotai-cn` | `https://api.moonshot.cn/v1` | `MOONSHOT_API_KEY` |
| Kimi Coding | `kimi-coding` | `https://api.kimi.com/coding/v1` | `KIMI_API_KEY` 或 OAuth 登录 |

四家内置 provider 全部使用 `api_impls/openai_completions`（当前唯一的协议实现，基于官方 `openai` Python SDK）。其他 OpenAI 兼容端点（DeepSeek、OpenRouter、Together、vLLM、LM Studio 等）可经 `create_provider()` 接入，兼容层会按 base_url 自动适配，见[自定义 Provider](#自定义-provider)。

内置模型目录是构建期生成的静态数据（模型 id、上下文窗口、费率、思考级别表等），随包发布、离线可用。

## 安装

```bash
pip install nova-ai
```

要求 Python `>=3.12,<3.14`。运行时依赖：`openai>=2.0`、`pydantic>=2.0`、`json-repair>=0.58.4`、`httpx>=0.27`。

在 monorepo 仓库内开发时，使用根目录的 pixi 统一环境（editable 安装全部子包）：

```bash
pixi install --environment dev
```

## 快速上手

设置环境变量（以 Volcengine 为例）：

```bash
export VOLCENGINE_API_KEY="your-api-key"
```

构建 `Models` 集合、取模型、发起流式调用：

```python
import asyncio
from nova_ai import Context, UserMessage, builtin_models

models = builtin_models()  # 注册全部内置 provider 的 Models 集合
model = models.get_model("volcengine", "doubao-seed-2-0-mini-260428")

context = Context(messages=[UserMessage(content="用一句话介绍你自己")])


async def main():
    # stream_simple 同步返回事件流；鉴权解析在后台进行
    stream = models.stream_simple(model, context)
    async for event in stream:
        if event.type == "text_delta":
            print(event.delta, end="", flush=True)

    # done / error 事件到达后，result() 解析出最终 AssistantMessage
    message = await stream.result()
    usage = message.usage
    print(f"\ntokens: {usage.input} -> {usage.output}  cost: ${usage.cost.total:.6f}")


asyncio.run(main())
```

不需要逐事件消费时，用非流式接口直接拿最终消息：

```python
message = await models.complete_simple(model, context)
print(message.content[0].text)
```

带工具调用的一个完整回合：

```python
from nova_ai import StopReason, TextContent, Tool, ToolResultMessage

context = Context(
    messages=[UserMessage(content="北京今天天气怎么样？")],
    tools=[
        Tool(
            name="get_weather",
            description="获取指定城市的天气",
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string", "description": "城市名称"}},
                "required": ["city"],
            },
        )
    ],
)


async def tool_round():
    message = await models.complete_simple(model, context)
    if message.stop_reason != StopReason.TOOL_USE:
        return message

    context.messages.append(message)  # 助手消息入上下文
    for call in (c for c in message.content if c.type == "toolCall"):
        result = run_tool(call.name, call.arguments)  # 你的工具实现
        context.messages.append(
            ToolResultMessage(
                tool_call_id=call.id,
                tool_name=call.name,
                content=[TextContent(text=result)],
            )
        )
    return await models.complete_simple(model, context)  # 继续对话
```

## Provider 与模型

### 内置 provider 工厂

每个内置 provider 有独立工厂函数，按需注册可避免携带用不到的目录：

```python
from nova_ai import create_models, volcengine_provider, kimi_coding_provider

models = create_models()
models.set_provider(volcengine_provider())
models.set_provider(kimi_coding_provider())
```

`builtin_providers()` 返回四家工厂实例的列表；`builtin_models()` 等价于 `create_models()` 后注册全部内置 provider。

### 查询

```python
models.get_providers()                    # 全部已注册 Provider
models.get_provider("volcengine")         # 按 id 查 Provider
models.get_models()                       # 全部模型；get_models("volcengine") 限定单家
models.get_model("volcengine", "doubao-seed-2-0-mini-260428")  # 精确查找，未命中返回 None
await models.get_available()              # 已配置鉴权的 provider 的模型（异步）
```

### 静态目录读取

不构造 `Models` 集合也可以直接读内置目录：

```python
from nova_ai import VOLCENGINE_MODELS, get_volcengine_model, list_volcengine_models

model = get_volcengine_model("doubao-seed-2-0-mini-260428")  # 未命中抛 KeyError
for model_id, m in list_volcengine_models().items():
    print(model_id, m.name)
```

其余三家同形：`MOONSHOTAI_MODELS` / `MOONSHOTAI_CN_MODELS` / `KIMI_CODING_MODELS` 与对应的 `get_*_model` / `list_*_models`；跨 provider 的便捷函数是 `get_builtin_models(provider_id=None)` 与 `get_builtin_model(provider_id, model_id)`。

### 动态模型目录

`create_provider(fetch_models=...)` 让 provider 的模型列表可刷新：`Models.refresh()` 先从 `ModelsStore` 恢复上次缓存的目录（离线可用），再解析凭据走网络拉取，新目录经世代校验后发布并持久化——被 `set_provider` / `delete_provider` 取代的在途刷新不会覆盖新目录。

```python
result = await models.refresh(providers=["volcengine"], force=True)
# result == {"aborted": False, "errors": {provider_id: exception, ...}}  —— 错误按 provider 收集，不抛出
```

`ModelsStore` 是持久化抽象（`read` / `write` / `delete`，条目为 `ModelsStoreEntry`），默认 `InMemoryModelsStore` 只存进程内存；需要跨进程缓存时注入自己的实现（`create_models(models_store=...)`）。

## 鉴权

### 解析链

每次请求的鉴权按以下优先级解析（凭据占有 provider——存在已存储 credential 时不再咨询环境变量）：

1. **调用方覆盖**：`StreamOptions.api_key`（`complete` / `stream` 系列调用的 options 参数）；
2. **已存储 credential**：`CredentialStore` 中的 API key 或 OAuth credential；OAuth token 剩余有效期不足 5 分钟时在 store 锁内自动刷新（并发请求只刷一次，刷新有 15 秒硬超时）；
3. **环境变量**：每个 provider 固定的环境变量名（见下表）。

解析失败（无任何可用凭据）不会抛出——调用以 `error` 事件收尾。

### 显式覆盖与检查

```python
from nova_ai import SimpleStreamOptions

# 单次调用显式指定 key（适合短寿命 token，优先于一切其他来源）
options = SimpleStreamOptions(api_key="explicit-key")
message = await models.complete_simple(model, context, options)

# 检查 provider 是否已配置鉴权（不触发网络刷新）
check = await models.check_auth("volcengine")  # AuthCheck(type="api_key", source="VOLCENGINE_API_KEY") 或 None

# 直接取解析结果（含来源标注）
result = await models.get_auth("volcengine")
if result:
    print(result.source)  # "stored credential" / 环境变量名 / "OAuth"
```

### 登录与登出

`kimi-coding` 除 API key 外内置 OAuth（device code）登录；`openai_codex_oauth`（浏览器回调 + device code 双模式）可用于自建 OpenAI Codex provider：

```python
from nova_ai import AuthEvent, AuthPrompt


class CliInteraction:
    """AuthInteraction 契约：prompt() 索取输入，notify() 接收登录进度事件。"""

    async def prompt(self, prompt: AuthPrompt) -> str:
        return input(f"{prompt.message}: ")

    def notify(self, event: AuthEvent) -> None:
        if event.verification_uri_complete:
            print("请打开:", event.verification_uri_complete)


credential = await models.login("kimi-coding", "oauth", CliInteraction())
await models.logout("kimi-coding")  # 删除已存储 credential
```

### CredentialStore 注入

credential 的持久化由调用方注入的 `CredentialStore` 负责，默认 `InMemoryCredentialStore` 只存进程内存（`nova-ai` 自身不落盘任何密钥）。协议四个方法：

```python
class CredentialStore(Protocol):
    async def read(self, provider_id: str) -> Optional[Credential]: ...
    async def list(self) -> List[CredentialInfo]: ...
    async def modify(self, provider_id: str, fn) -> Optional[Credential]: ...  # 唯一写路径，按 provider 串行化
    async def delete(self, provider_id: str) -> None: ...


models = create_models(credential_store=MyFileCredentialStore("~/.myapp/auth.json"))
```

`modify(provider_id, fn)` 是串行化的读-改-写：`fn` 收到当前 credential（可能为 `None`），返回新 credential（返回 `None` 表示不变更）。OAuth 刷新与登录写入都走这一条路径，应用实现文件存储时只需保证这四个字法语义。

credential 两种形态：`ApiKeyCredential(key, env)` 与 `OAuthCredential(access, refresh, expires, ...)`（扩展字段在序列化往返中保留）。

### 环境变量

| Provider id | 环境变量 |
|-------------|---------|
| `volcengine` | `VOLCENGINE_API_KEY` |
| `moonshotai` | `MOONSHOT_API_KEY` |
| `moonshotai-cn` | `MOONSHOT_API_KEY` |
| `kimi-coding` | `KIMI_API_KEY` |

`get_env_api_key(provider)` 还内置 `openai`、`deepseek`、`xai`、`groq`、`openrouter`、`mistral`、`zai`、`google` 等常见 provider id 的映射（自定义 provider 可直接复用），完整表见 `src/nova_ai/utils/env.py`。`StreamOptions.env` 可注入 provider 级环境变量，优先级高于进程环境。

另有一个行为开关：`NOVA_CACHE_RETENTION=long` 将 prompt 缓存保留策略默认值从 `short` 提升为 `long`。

### 请求头合并与 transform_headers

请求头按 **auth 解析结果 → `Model.headers` → `StreamOptions.headers`** 的顺序合并（大小写不敏感，后者覆盖前者；`StreamOptions.headers` 中值为 `None` 表示抑制同名默认头）。在此之上，`StreamOptions.transform_headers` 是最后一道钩子：在合并完成后、派发给协议实现前运行一次，可同步或异步返回新的 headers；该字段由 `Models` 层消费，协议实现永远看不到它。

## 工具调用

工具即 JSON Schema 描述，`Context.tools` 传入 `Tool` 列表：

```python
from nova_ai import Tool

tool = Tool(
    name="get_weather",
    description="获取指定城市的天气",
    parameters={  # 任意 JSON Schema dict
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)
```

模型决定调用工具时，流里产出 `toolcall_start → toolcall_delta* → toolcall_end` 序列。增量期间，`partial.content[content_index]` 上的 `ToolCall.arguments` 是**部分 JSON 的增量解析快照**（基于 `json-repair` 的节流解析，始终为合法 dict）——适合做进度展示，不要当作最终参数执行；终值以 `toolcall_end` 事件的 `tool_call.arguments` 为准：

```python
async for event in models.stream_simple(model, context):
    if event.type == "toolcall_delta":
        snapshot = event.partial.content[event.content_index].arguments  # 部分快照，可能缺键
    elif event.type == "toolcall_end":
        call = event.tool_call  # 终态 ToolCall：id / name / arguments 完整
```

需要在自有流式管线里做同样的防御性解析时，可直接使用公开的 `parse_streaming_json(partial_json_str)`：任意不完整 JSON 都返回合法对象，解析失败返回 `{}`。

工具执行结果以 `ToolResultMessage` 回灌上下文（见[快速上手](#快速上手)的完整回合）。`ToolResultMessage` 字段：`tool_call_id`、`tool_name`、`content`（`TextContent` / `ImageContent` 列表）、`is_error`、`details`（任意 JSON 值，工具自定义形态）。

## 图片输入

`Model.input_types` 含 `"image"` 的模型支持图片输入。把 `ImageContent`（base64 数据 + MIME 类型）放进 `UserMessage.content` 列表即可，发送时自动转为 OpenAI `image_url` data URL 形态：

```python
import base64
from nova_ai import ImageContent, TextContent

if "image" not in model.input_types:
    raise ValueError(f"{model.id} 不支持图片输入")

context = Context(
    messages=[
        UserMessage(
            content=[
                TextContent(text="描述这张图片"),
                ImageContent(
                    mime_type="image/jpeg",
                    data=base64.b64encode(open("photo.jpg", "rb").read()).decode(),
                ),
            ]
        )
    ],
)
```

上下文回放到不支持图片的模型时，`transform_messages()`（见[跨模型交接](#跨模型交接)）会自动把图片块降级为占位文本，不会把无法消费的 data URL 发给文本模型。

## Thinking / Reasoning

`stream_simple` / `complete_simple` 接受统一的思考级别，协议实现按厂商分派为具体参数：

```python
from nova_ai import SimpleStreamOptions, ThinkingLevel

options = SimpleStreamOptions(reasoning=ThinkingLevel.HIGH)
# minimal | low | medium | high | xhigh | max；不传（None）= 关闭思考，不发送任何 reasoning 参数
message = await models.complete_simple(model, context, options)
```

- 仅 `Model.reasoning == True` 的模型支持思考；不支持时级别被忽略。
- 实际生效级别会吸附到模型支持集：模型经 `thinking_level_map` 声明级别映射（键缺失 = 回落原级别，显式 `None` = 该级别不受支持；`xhigh` / `max` 默认不受支持，须显式声明）。用 `get_supported_thinking_levels(model)` 查询支持集，`clamp_thinking_level(model, level)` 手动吸附。
- `openai_completions` 按 `compat.thinking_format` 分派参数形态：`openai`（`reasoning_effort`）、`deepseek`、`zai`、`qwen`、`openrouter`（`reasoning: {effort}`）、`together`、`ant-ling`、`string-thinking`、`chat-template` / `baseten`（chat template 变量）等；共享 `max_tokens` 的端点可用 `thinking_budgets` 给各级别设 token 预算（自动为答案保留余量）。
- 思考内容经 `thinking_start / thinking_delta / thinking_end` 事件流式产出；一条流中思考增量合并进同一个 thinking 块、文本增量合并进同一个 text 块（`content_index` 稳定）。

## 停止原因

`AssistantMessage.stop_reason` 为 `StopReason` 枚举：

| 值 | 含义 |
|----|------|
| `StopReason.PENDING`（`"pending"`） | 流中瞬态初值，尚未收到 finish_reason |
| `StopReason.STOP`（`"stop"`） | 正常结束 |
| `StopReason.LENGTH`（`"length"`） | 达到长度限制 |
| `StopReason.TOOL_USE`（`"toolUse"`） | 触发工具调用 |
| `StopReason.ERROR`（`"error"`） | 发生错误（`error_message` 有详情） |
| `StopReason.ABORTED`（`"aborted"`） | 被中止 |

`AssistantMessage.raw_stop_reason` 保留厂商原始 finish_reason 字符串（未映射）。端点不保证发 finish_reason 时（`compat.supports_finish_reason=False`），流尾按内容推断：有工具调用块记 `TOOL_USE`，否则记 `STOP`。

## 错误处理与请求中止

### 错误即事件，不是异常

经 `Models` / `Provider` 发起的调用**不抛异常**：一切失败——未知 provider、鉴权缺失、网络错误、厂商 4xx/5xx——都编码为 `error` 事件终止流，`complete` 系列随之返回 `stop_reason=ERROR` 的 `AssistantMessage`：

```python
async for event in models.stream_simple(model, context):
    if event.type == "error":
        # event.error 是终态 AssistantMessage，含 error_message 与已产生的部分内容/用量
        print(event.reason, event.error.error_message)  # reason: "error" | "aborted"
```

```python
message = await models.complete_simple(model, context)
if message.stop_reason == StopReason.ERROR:
    print("请求失败:", message.error_message)
```

`is_context_overflow(message, context_window=model.context_window)` 可区分上下文溢出类错误（内置多家厂商的错误模式匹配，也覆盖静默截断场景）。

### 中止请求

```python
from nova_ai import AbortController, SimpleStreamOptions

controller = AbortController()
options = SimpleStreamOptions(signal=controller.signal)

stream = models.stream_simple(model, context, options)
controller.abort()  # 主动关闭底层 HTTP 流；已收到的内容块正常收尾，
                    # 最终以 ErrorEvent(reason="aborted") 结束
```

### 调试钩子与重试

- `StreamOptions.on_payload(params, model)`：请求体构建完成后、发送前调用；返回新 dict（或 awaitable）可替换请求体，返回 `None` 表示不修改。
- `StreamOptions.on_response(ProviderResponse, model)`：拿到原始 HTTP 状态码与响应头。
- 重试在请求层执行（`StreamOptions.max_retries`，默认 `0` 不重试），退避等待可被 abort 打断；`max_retry_delay_ms` 封顶服务器 `Retry-After` 要求的等待（默认 60000ms，`0` 为不封顶）。SDK 内建重试恒为关闭——它无法被中止打断。
- `StreamOptions.timeout`（秒）设置单次请求超时。

## 自定义 Provider

任意 OpenAI 兼容端点经 `create_provider()` 接入：

```python
from nova_ai import Model, ModelCost, ProviderAuth, create_models, create_provider, env_api_key_auth
from nova_ai.api_impls import openai_completions

model = Model(
    id="my-model",
    name="My Model",
    api="openai-completions",
    provider="my-provider",
    base_url="https://llm.example.com/v1",
    reasoning=False,
    input_types=["text"],
    cost=ModelCost(input=1.0, output=2.0, cache_read=0.1, cache_write=0.0),  # $/百万 tokens
    context_window=131072,
    max_tokens=8192,
)

provider = create_provider(
    id="my-provider",
    name="My Provider",
    base_url="https://llm.example.com/v1",
    models=[model],
    api=openai_completions,  # 协议实现模块；也可传 {"openai-completions": impl, ...} 按 model.api 分派
    auth=ProviderAuth(api_key=env_api_key_auth("My API Key", ["MY_PROVIDER_API_KEY"])),
)

models = create_models()
models.set_provider(provider)
message = await models.complete_simple(model, Context(messages=[UserMessage(content="你好")]))
```

### 兼容性配置（compat）

`openai_completions` 按 provider id 与 base_url 自动检测端点兼容性（识别 DeepSeek、Z.ai、Together、Moonshot、OpenRouter、NVIDIA、xAI、Cerebras、Cloudflare、Volcengine 等；未知端点按 OpenAI 标准行为处理）。`Model.compat`（`OpenAICompletionsCompat`）的非 `None` 字段逐字段覆盖检测结果：

```python
from nova_ai import OpenAICompletionsCompat, ThinkingFormat

model = Model(
    # ...其余字段...
    compat=OpenAICompletionsCompat(
        supports_store=False,                    # 不发送 store 字段
        supports_developer_role=False,           # system 角色而非 developer
        max_tokens_field="max_tokens",           # 用 max_tokens 而非 max_completion_tokens
        supports_reasoning_effort=False,         # 端点不接受 reasoning_effort
        supports_strict_mode=False,              # 工具定义不带 strict 字段
        thinking_format=ThinkingFormat.DEEPSEEK, # 思考参数形态
        requires_reasoning_content_on_assistant_messages=True,  # 回放时携带 reasoning_content
    ),
)
```

常用字段（全部 `Optional`，缺省走自动检测）：`supports_store`、`supports_developer_role`、`supports_reasoning_effort`、`supports_usage_in_streaming`、`supports_finish_reason`、`max_tokens_field`、`requires_tool_result_name`、`requires_assistant_after_tool_result`、`requires_thinking_as_text`、`thinking_format`、`supports_strict_mode`、`requires_reasoning_content_on_assistant_messages`、`supports_long_cache_retention`、`cache_control_format`、`deferred_tools_mode`、`zai_tool_stream`、`open_router_routing`、`vercel_gateway_routing` 等，完整定义见 `src/nova_ai/types/compat.py`。

## 流式事件参考

`stream` / `stream_simple` 返回的 `AssistantMessageEventStream` 是异步迭代器；`done` / `error` 事件到达后流结束，`await stream.result()` 解析出最终 `AssistantMessage`。所有增量事件携带 `partial`——当前累积中的 `AssistantMessage`（**活引用**，流式期间被原地更新，请勿跨事件留存）。

| `event.type` | 含义 | 关键属性 |
|--------------|------|----------|
| `start` | 流开始 | `partial` |
| `text_start` | 文本块开始 | `content_index` |
| `text_delta` | 文本增量 | `content_index`、`delta` |
| `text_end` | 文本块结束 | `content_index`、`content`（全文） |
| `thinking_start` | 思考块开始 | `content_index` |
| `thinking_delta` | 思考增量 | `content_index`、`delta` |
| `thinking_end` | 思考块结束 | `content_index`、`content`（全文） |
| `toolcall_start` | 工具调用块开始 | `content_index` |
| `toolcall_delta` | 工具参数增量 | `content_index`、`delta`（JSON 片段） |
| `toolcall_end` | 工具调用块结束 | `content_index`、`tool_call`（终态 `ToolCall`） |
| `done` | 完成 | `reason`（`StopReason`）、`message`（最终消息） |
| `error` | 错误/中止 | `reason`（`"error"` / `"aborted"`）、`error`（终态消息） |

典型序列：`start → (thinking_* | text_* | toolcall_*)* → done | error`。事件流不可复用：结束后不可再迭代。

## 跨模型交接

把一段对话从模型 A 回放到模型 B 时，`transform_messages(messages, model)` 做跨厂商规范化：思考块按目标模型保留或转文本、剥离目标不认识的签名、规范化工具调用 id、为孤立工具调用补合成 `ToolResultMessage`、把目标模型不支持的图片降级为占位文本，并跳过 `ERROR` / `ABORTED` 的不完整助手消息。协议实现内部已对每个请求自动执行；在自有编排层手动回放上下文时可直接调用。

## 示例

[`examples/`](./examples) 目录含 4 个可直接运行的脚本（默认离线 mock 运行，真实调用在未配置 key 时自动跳过）：

- `01_quickstart.py` —— 最小用法：mock 协议模块 + `builtin_models()` 真实调用
- `02_stream_events.py` —— 流式事件类型与消费顺序
- `03_models_and_providers.py` —— Models 集合、自定义 provider 注册、动态模型目录
- `04_auth.py` —— 鉴权解析链：环境变量、`options.api_key` 覆盖、动态 key 注入

## 开发

```bash
# monorepo 根目录：安装 dev 环境后跑本包测试（排除真实 API 集成测试）
pixi run -e dev test-ai

# 或在子包内直接跑
cd packages/nova_ai
pixi run -e dev pytest tests -m "not integration"

# 真实 API 集成测试（tests/integration/，需 VOLCENGINE_API_KEY / KIMI_API_KEY 等）
pixi run -e dev pytest tests/integration
```

## License

MIT
