# nova_ai 架构设计

## 1. 定位

`nova_ai` 是 Nova 单体仓库的最底层子包，提供**多厂商 LLM 提供商统一抽象层**。向上层的 `nova_agent`（事件驱动 Agent 框架）和 `nova_harness`（高阶 SDK）提供标准化的模型调用、消息类型、流式事件和注册表能力。

```
nova_harness (高阶 SDK)
    ↑
nova_agent (Agent 核心框架)
    ↑
nova_ai  ← 本包（LLM 提供商抽象层）
```

## 2. 模块划分

```
src/nova_ai/
├── types/              # 纯数据类型定义（Pydantic BaseModel）
│   ├── base_model.py   # NovaBaseModel：统一基类，提供 validate_assignment
│   ├── enums.py        # 枚举：KnownApi, KnownProvider, StopReason, ThinkingFormat...
│   ├── content.py      # 内容块：TextContent, ThinkingContent, ToolCall, ImageContent
│   ├── messages.py     # 消息：UserMessage, AssistantMessage, ToolResultMessage, Context
│   ├── events.py       # 流式事件：TextStart/Delta/End, ToolCall*, DoneEvent, ErrorEvent
│   ├── usage.py        # 用量与成本：Usage, Cost
│   ├── model.py        # 模型定义：Model, ModelCost, thinking_level_map
│   ├── compat.py       # 兼容性配置：OpenAICompletionsCompat, OpenRouterRouting...
│   ├── stream_options.py   # 流选项：StreamOptions, SimpleStreamOptions, ProviderResponse
│   └── registry.py     # 注册表类型：ApiProviderRecord
├── providers/          # 具体提供商实现
│   └── openai_completions.py   # OpenAI Completions API 流式处理（核心）
├── registry/           # 注册表管理
│   ├── api_registry.py     # API 提供者注册表
│   ├── model_registry.py   # 模型注册表
│   └── builtins.py         # 内置组件注册
├── streaming/          # 流式处理高层 API
│   ├── event_stream.py     # 事件流基础设施
│   └── api.py              # stream(), complete(), stream_simple()...
├── models/             # 厂商模型静态数据
│   └── volcengine.py       # 火山引擎模型定义（DeepSeek V3.2 / V4）
├── utils/              # 工具函数
│   ├── env.py              # 环境变量 API 密钥获取
│   ├── json_parser.py      # 流式 JSON 解析
│   ├── surrogate.py        # 字符串代理字符清理
│   ├── stream_options.py   # 流选项构建与 reasoning 裁剪
│   ├── message_transformer.py  # 跨提供商消息转换
│   ├── model_utils.py      # 成本计算、思考级别支持检测
│   └── overflow.py         # 上下文溢出检测
└── __init__.py         # 根包统一导出（97 个公共 API）
```

## 3. 核心数据流

### 3.1 流式调用流程

```
调用方
  │
  ▼
stream(model, context, options)
  │
  ▼
build_params(model, context, options)
  ├── 标准字段 → params (model, messages, temperature, max_tokens, tools...)
  └── 厂商特定字段 → extra_body (thinking, provider, prompt_cache_key...)
  │
  ▼
convert_messages(model, context, compat)
  ├── 消息格式转换（UserMessage → OpenAI ChatCompletionMessageParam）
  └── 兼容性处理（system/developer 角色、thinking 块转换、tool ID 规范化）
  │
  ▼
OpenAI AsyncOpenAI.create(**params)
  │
  ▼
process_stream(chunk_iterator)
  ├── 块状态管理：ensure_text_block() / ensure_thinking_block() / ensure_tool_call_block()
  ├── 支持并行 tool calls（tool_call_blocks_by_index / by_id）
  ├── 事件发射：TextDeltaEvent, ThinkingDeltaEvent, ToolCallDeltaEvent...
  └── 取消信号处理：signal.aborted 时关闭底层流并收尾已有 block
  │
  ▼
AssistantMessageEventStream（事件流返回给调用方）
```

### 3.2 消息类型体系

```
Message (Union)
  ├── UserMessage
  │     └── content: str | List[TextContent | ImageContent]
  ├── AssistantMessage
  │     └── content: List[TextContent | ThinkingContent | ToolCall]
  └── ToolResultMessage
        └── content: List[TextContent | ImageContent]
              └── tool_call_id, tool_name, is_error

Context
  ├── system_prompt: Optional[str]
  ├── messages: List[Message]
  └── tools: Optional[List[Tool]]
```

## 4. 关键设计决策

### 4.1 Pydantic v2 统一数据建模

全库使用 `NovaBaseModel`（继承 `pydantic.BaseModel`）替代标准 `dataclass`，核心收益：

- **`validate_assignment = True`**：运行时字段校验，防止非法赋值
- **JSON 序列化内置**：`model_dump()` / `model_dump_json()` 统一接口
- **兼容 mashumaro 风格**：逐步替换原有 `DataClassJSONMixin` 代码

### 4.2 `extra_body` 架构

OpenAI Python SDK 与 TypeScript SDK 传参机制不同：Python SDK 的 `create(**kwargs)` 会严格校验参数名，所有非标准字段必须通过 `extra_body` 透传。

`build_params()` 内部维护两个 dict：
- `params`：标准字段直接传入 `create()`
- `extra_body`：厂商特定字段自动收集后统一塞入

此设计兼容：DeepSeek thinking、OpenRouter routing、Vercel Gateway routing、OpenAI prompt caching 等。

### 4.3 `thinking_level_map` 设计

不同厂商对 thinking/reasoning 的级别命名不同：

| 级别 | DeepSeek (Volcengine) | OpenRouter | Together |
|------|----------------------|------------|----------|
| off | disabled | off | false |
| low | — | low | — |
| medium | — | medium | — |
| high | high | high | high |
| xhigh | max | — | — |

`Model.thinking_level_map: Dict[str, Optional[str]]` 将标准化的 6 级（off/minimal/low/medium/high/xhigh）映射到厂商特定值，`None` 表示该级别不受支持。

### 4.4 流式事件模型

10 种事件覆盖完整流式生命周期：

```
start
├── text_start → text_delta × N → text_end
├── thinking_start → thinking_delta × N → thinking_end
├── toolcall_start → toolcall_delta × N → toolcall_end
└── done / error
```

每个事件携带 `partial: AssistantMessage`，调用方可随时获取当前累积的完整消息状态。

### 4.5 兼容性自动检测 + 显式覆盖

`detect_compat(model)` 基于 `provider` + `base_url` 自动推断兼容性配置（thinking_format、supports_store 等）。同时 `Model.compat` 字段允许显式覆盖任何自动检测值，满足特殊部署场景。

### 4.6 注册表设计

双层注册表解耦 API 类型与模型数据：

- **ApiProviderRegistry**：按 `api`（如 `openai-completions`）注册流式处理函数
- **ModelRegistry**：按 `provider` + `model_id` 双 key 管理模型定义

内置组件通过 `register_all_builtins()` 在包导入时自动注册，支持运行时 `reset_registry()` 重置。

## 5. 测试策略

测试 suite 覆盖：
- **类型层**：所有 Pydantic 模型的序列化/反序列化、字段校验、默认值
- **工具层**：成本计算、消息转换、溢出检测、流选项构建
- **Provider 层**：`build_params` 参数构建、`convert_messages` 消息转换、`detect_compat` 兼容性检测
- **Registry 层**：API/模型注册、查询、注销、重置
- **导入层**：根包 97 个公共 API 全部可导入
- **集成层**：DeepSeek/Volcengine 真实模型调用（40 个集成测试）

运行：
- 单元测试：`pytest -m "not integration"`（158 passed）
- 集成测试：`pytest tests/test_integration_deepseek.py`（40 passed）
