<!-- AGENTS.md - nova_ai 包级指南 -->

# nova_ai —— 统一的 LLM 提供商抽象层

> 本文件面向 AI Coding Agent 编写。如果你不了解 `nova_ai` 这个子包，请从这里开始阅读。

## 项目概览

`nova_ai` 是 Nova monorepo 的最底层子包，职责是为上层（`nova_agent`、`nova_harness` 等）提供**多厂商统一的流式调用接口**。它将 OpenAI、Anthropic、Google、Volcengine 等厂商的差异封装在内部，对外暴露一致的 `stream` / `complete` / `stream_simple` / `complete_simple` API。

- **源码包名**：`nova_ai`
- **版本**：`0.1.0`
- **目标语言**：Python `>=3.9,<3.13`
- **项目语言**：代码注释与文档主要使用**中文**
- **License**：MIT
- **作者**：Liujinming

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python `>=3.9,<3.13` |
| 包管理器 | **Poetry**（独立 `pyproject.toml`） |
| 格式化 | `black`（目标语法版本 `py311`） |
| Import 排序 | `isort`（`profile = "black"`） |
| 序列化 | `pydantic`（`BaseModel`） |
| 异步运行时 | `asyncio` |
| 开发依赖 | `pre-commit`、`pytest`、`sniffio` |
| 关键运行时依赖 | `openai >= 1.109.1`、`pydantic >= 2.0`、`json-repair >= 0.58.4` |

**未使用** Pydantic、Mypy、Tox、Makefile、Docker 或 CI/CD。

---

## 代码组织与模块划分

源码位于 `src/nova_ai/`，按职责分为 8 个子包/模块：

```
src/nova_ai/
├── __init__.py              # 全量重新导出 + 初始化（HTTP代理、内置注册）
├── core/                    # 基础类型定义
│   ├── enums.py             # Api、Provider、StopReason、ThinkingLevel 等枚举
│   ├── content.py           # TextContent、ThinkingContent、ToolCall、ImageContent
│   ├── messages.py          # UserMessage、AssistantMessage、ToolResultMessage、Context
│   ├── model.py             # Usage、Cost
│   └── serialize.py         # content 字段的自定义序列化/反序列化函数
├── models/                  # 厂商模型静态数据与查询
│   ├── base.py              # Model、ModelCost、calculate_cost、supports_xhigh_thinking
│   ├── openai.py            # OPENAI_MODELS 字典
│   ├── anthropic.py         # ANTHROPIC_MODELS 字典
│   ├── google.py            # GOOGLE_MODELS 字典
│   └── volcengine.py        # VOLCENGINE_MODELS 字典
├── apis/                    # API 协议实现
│   ├── openai_completions.py # OpenAI Completions API 的流式处理（当前唯一完整实现）
│   └── __init__.py          # ProviderStreamOptions 类型别名
├── registry/                # 注册表（全局单例）
│   ├── api_registry.py      # ApiRegistry：按 api 类型注册流式函数
│   ├── model_registry.py    # ModelRegistry：按 provider + model_id 注册模型
│   └── builtins.py          # 内置 API 提供者与模型的自动注册/重置
├── streaming/               # 流式事件与高层 API
│   ├── events.py            # StartEvent、TextDeltaEvent、ToolCallEndEvent、DoneEvent 等
│   ├── event_stream.py      # EventStream、AssistantMessageEventStream（异步迭代器 + Future）
│   └── api.py               # stream()、complete()、stream_simple()、complete_simple()
├── auth/                    # 云厂商鉴权辅助（纯检测，不管理密钥）
│   ├── bedrock.py           # 检测 AWS/Bedrock 多种凭证源
│   └── vertex.py            # 检测 Google Vertex ADC 凭证
├── compat/                  # 兼容性配置
│   ├── openai.py            # OpenAICompletionsCompat、OpenAIResponsesCompat
│   └── routing.py           # OpenRouterRouting、VercelGatewayRouting
└── utils/                   # 工具函数
    ├── env.py               # 从环境变量读取各厂商 API key
    ├── copilot.py           # GitHub Copilot 动态请求头构造
    ├── json_parser.py       # 流式 JSON 片段解析（json-repair 封装）
    ├── message_transformer.py # 跨厂商消息转换（思考块处理、工具调用ID规范化）
    ├── stream_options.py    # StreamOptions、SimpleStreamOptions、ThinkingBudgets
    ├── surrogate.py         # Unicode 代理项清理
    ├── overflow.py          # 上下文溢出检测
    └── http_proxy.py        # HTTP/HTTPS 代理环境变量读取与配置
```

### 运行时架构要点

1. **全局注册表**  
   `api_registry.py` 与 `model_registry.py` 各维护一个全局单例（`_registry`、`_model_registry`）。包初始化时（`__init__.py`），`register_all_builtins()` 会将内置的 API 提供者与模型注册进去。上层代码通过 `stream(model, context)` 时，`api.py` 先查 `model.api` 对应的提供者记录，再调用其 `stream()` 函数。

2. **事件流模型**  
   所有流式调用返回 `AssistantMessageEventStream`，它继承自通用 `EventStream[T, R]`。内部使用 `asyncio.Queue` 缓冲事件，消费者用 `async for` 迭代；同时内部持有一个 `asyncio.Future`，在收到 `DoneEvent` 或 `ErrorEvent` 时设置结果，支持 `await stream.result()` 获取最终 `AssistantMessage`。

3. **OpenAI Completions 实现**  
   当前唯一完整的 API 协议实现位于 `apis/openai_completions.py`。它使用官方 `openai.AsyncOpenAI` 客户端，将内部 `Message` / `Context` 转换为 `ChatCompletionMessageParam`，发送 `chat.completions.create(stream=True)`，然后把 SSE chunk 映射为标准事件推入 `AssistantMessageEventStream`。该模块同时承担了大量兼容性逻辑：
   - 根据 `provider` 和 `base_url` 自动检测 `OpenAICompletionsCompat`（是否支持 `store`、`developer` 角色、`reasoning_effort`、Mistral 工具 ID 规范等）。
   - 支持 OpenRouter 路由、`Vercel AI Gateway` 路由。
   - 支持 reasoning 内容（`reasoning_content`、`reasoning`、`reasoning_text` 等字段）的增量提取。
   - 工具调用参数的流式 JSON 解析（`parse_streaming_json`）。

4. **消息转换与跨模型兼容**  
   `utils/message_transformer.py` 的 `transform_messages()` 会在请求前执行两趟处理：
   - 第一趟：思考块保留/转文本、工具调用 ID 规范化（跨模型时删除 `thought_signature`）。
   - 第二趟：为孤立的工具调用插入合成 `ToolResultMessage`；跳过 `stop_reason` 为 `ERROR` / `ABORTED` 的不完整助手消息。

5. **序列化层**  
   所有数据类均继承 `mashumaro.mixins.json.DataClassJSONMixin`，可直接调用 `.to_dict()` / `.from_dict()` / `.to_json()` / `.from_json()`。`messages.py` 中 `UserMessage.content` 使用自定义 `serialize_content` / `deserialize_content` 处理 `str | List[TextContent|ImageContent]` 的联合类型。

---

## 构建与开发命令

### 安装依赖

```bash
cd packages/nova_ai
poetry install
```

### 格式化

```bash
poetry run black src/
poetry run isort src/
```

### 构建与发布

```bash
poetry build      # 生成 wheel / sdist
poetry publish    # 如需发布到 PyPI
```

---

## 代码风格指南

- **类名**：`PascalCase`
- **函数 / 变量**：`snake_case`
- **常量**：`UPPER_CASE`
- **导入排序**：使用 `isort`，配置为 `profile = "black"`、`multi_line_output = 3`、`include_trailing_comma = true`
- **格式化**：`black`，目标版本 `py311`
- **注释与文档字符串**：以**中文**为主，保持与现有代码一致
- **数据建模**：优先使用 `pydantic.BaseModel` 做数据建模与 JSON 序列化
- **类型注解**：代码中已大量使用类型注解，但未配置 `mypy` 静态检查

---

## 测试说明

- `pyproject.toml` 已将 `pytest` 声明为开发依赖。
- **当前包内没有任何测试目录或测试文件**。
- 如需补充测试，建议在包根目录新建 `tests/` 并按模块结构组织：

```
tests/
├── test_core/
├── test_streaming/
└── test_providers/
```

运行方式：

```bash
cd packages/nova_ai
poetry run pytest
poetry run pytest --cov=nova_ai --cov-report=html
```

---

## 安全注意事项

1. **API Key 来源**  
   本包不持久化存储任何密钥，全部通过环境变量按 `provider` 名称映射读取。映射逻辑在 `utils/env.py`：`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`GITHUB_TOKEN` 等。特殊路径：
   - GitHub Copilot 依次尝试 `COPILOT_GITHUB_TOKEN`、`GH_TOKEN`、`GITHUB_TOKEN`。
   - Anthropic 优先 `ANTHROPIC_OAUTH_TOKEN`，其次 `ANTHROPIC_API_KEY`。
   - Google Vertex 与 Amazon Bedrock 使用 ADC / AWS 配置文件，不通过单一环境变量密钥。

2. **字符串安全**  
   `utils/surrogate.py` 提供 `sanitize_surrogates()`，在将消息发送到 OpenAI API 前移除未配对的 Unicode 代理项对，避免请求体解析失败。

3. **兼容性层的潜在风险**  
   `models/openai_completions.py` 根据 `base_url` 子串自动推断兼容性标志（如 `volces.com`、`api.x.ai`、`mistral.ai` 等）。如果 URL 被恶意构造，可能诱导程序启用不安全的参数回退。虽然这属于使用层配置问题，但修改自动检测逻辑时需保持谨慎。

4. **HTTP 代理**  
   `utils/http_proxy.py` 在包导入时自动读取 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量并配置到 `AsyncOpenAI` 客户端。在敏感网络环境中，注意代理环境变量是否被意外设置。

---

## 开发惯例与给 AI Agent 的提示

- **不要假设有测试**：当前没有测试覆盖，修改核心类型或流式逻辑后建议手动验证或补充测试。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文，与现有风格一致。
- **序列化层**：若需新增数据类，请继承 `DataClassJSONMixin` 并放在对应模块（如 `core/content.py`）。
- **依赖新增**：若引入新的第三方库，需在 `pyproject.toml` 的 `[tool.poetry.dependencies]` 中声明，并执行 `poetry lock`（如有 lock 文件）。
- **新增厂商支持**：
  1. 在 `core/enums.py` 的 `KnownApi` / `KnownProvider` 添加枚举值。
  2. 在 `models/` 下新增模型字典（或直接在 `models/base.py` 动态注册）。
  3. 在 `apis/` 下实现流式处理函数（必须返回 `AssistantMessageEventStream`）。
  4. 在 `registry/builtins.py` 中条件导入并注册。
  5. 在 `utils/env.py` 中添加环境变量映射。
- **修改消息类型需谨慎**：`core/messages.py` 与 `core/content.py` 是整个 monorepo 的公共契约，字段增删可能影响 `nova_agent` 与 `nova_harness`。
- **流式事件不可复用**：`AssistantMessageEventStream` 一旦 `end()` 或收到 `DoneEvent`/`ErrorEvent`，队列即关闭，不能重新开始迭代。

---

## 版本与变更

- 当前版本：`0.1.0`（Alpha）
- 变更日志：`CHANGELOG.md`（当前为空）
