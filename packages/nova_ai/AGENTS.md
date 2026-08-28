<!-- AGENTS.md - nova_ai 包级指南 -->

# nova_ai —— 统一的 LLM 提供商抽象层

> 本文件面向 AI Coding Agent 编写。如果你不了解 `nova_ai` 这个子包，请从这里开始阅读。

## 项目概览

`nova_ai` 是 Nova monorepo 的最底层子包，职责是为上层（`nova_agent`、`nova_harness` 等）提供**多厂商统一的流式调用接口**。架构与 TypeScript 端 `pi/packages/ai` 对齐：以 `Models` 集合 + `Provider` 运行时单元 + API 协议实现（`api_impls/`）三层组织，对外暴露 `stream` / `complete` / `stream_simple` / `complete_simple` API。

- **源码包名**：`nova_ai`
- **版本**：`0.1.0`
- **目标语言**：Python `>=3.12,<3.14`
- **项目语言**：代码注释与文档主要使用**中文**
- **License**：MIT
- **作者**：Liujinming

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python `>=3.9,<3.13` |
| 包管理器 | **pixi**（monorepo 根目录统一 workspace；Poetry 配置保留为兼容） |
| 格式化 | `black`（目标语法版本 `py311`） |
| Import 排序 | `isort`（`profile = "black"`） |
| 数据建模 | `pydantic` v2 + `dataclass`（按根 `AGENTS.md` 决策顺序选型） |
| 异步运行时 | `asyncio` |
| 开发依赖 | `pytest`、`pytest-asyncio` |
| 关键运行时依赖 | `openai >= 1.109.1`、`pydantic >= 2.0`、`json-repair >= 0.58.4`、`httpx` |

**未使用** Mypy、Tox、Makefile、Docker 或 CI/CD。

---

## 代码组织与模块划分

源码位于 `src/nova_ai/`：

```
src/nova_ai/
├── __init__.py              # 全量重新导出（types / gateway / providers / auth / utils）
├── gateway/                 # Models 网关层（对齐 TS models.ts + models-store.ts）
│   ├── models.py            # Models 集合 + create_models + _lazy_stream（auth 网关 + provider 注册表）
│   ├── provider.py          # Provider / _DynamicProvider / create_provider（模型目录宿主 + 协议路由）
│   └── store.py             # ModelsStore / ProviderModelsStore 协议与 InMemory 实现
├── streaming.py             # EventStream、AssistantMessageEventStream（异步迭代器 + result Future）
├── signal.py                # AbortSignal / AbortController（对齐 TS AbortController 语义）
├── types/                   # 全部共享类型定义（契约层，无运行时行为）
│   ├── base_model.py        # NovaBaseModel（pydantic 基类，model_dump 默认 mode="json"）
│   ├── aliases.py           # ProviderEnv / ProviderHeaders 别名
│   ├── enums.py             # Api、ProviderId、StopReason、ThinkingLevel、ModelThinkingLevel 等
│   ├── content.py           # TextContent、ThinkingContent、ToolCall、ImageContent
│   ├── messages.py          # UserMessage、AssistantMessage、ToolResultMessage、Tool、Context
│   ├── model.py             # Model、ModelCost、Usage、Cost
│   ├── stream_options.py    # StreamOptions、SimpleStreamOptions、ThinkingBudgets（dataclass）
│   ├── events.py            # StartEvent、TextDeltaEvent、…、DoneEvent、ErrorEvent
│   ├── compat.py            # OpenAICompletionsCompat、OpenAIResponsesCompat、AnthropicMessagesCompat
│   └── auth.py              # Credential、CredentialStore、AuthContext、ApiKeyAuth、OAuthAuth 等
├── api_impls/               # API 协议实现
│   └── openai_completions.py # OpenAI Completions（当前唯一完整实现；模块即满足 ProviderStreams）
├── providers/               # 内置 provider 工厂与静态模型目录（每个 provider 一个子目录）
│   ├── all.py               # builtin_providers() / builtin_models()
│   ├── kimi_coding/         # models.py + provider.py（apiKey + OAuth device code 双鉴权）
│   ├── moonshotai/          # models.py + provider.py
│   ├── moonshotai_cn/       # models.py + provider.py
│   └── volcengine/          # models.py + provider.py
├── auth/                    # 鉴权行为层（类型定义在 types/auth.py，此处重导出）
│   ├── resolve.py           # resolve_provider_auth + ModelsError
│   ├── credential_store.py  # InMemoryCredentialStore（按 provider 串行化写）
│   ├── context.py           # DefaultAuthContext（env / 文件存在性）
│   ├── helpers.py           # env_api_key_auth / lazy_oauth
│   ├── oauth_page.py        # OAuth 回调成功/失败 HTML 页面
│   └── oauth/               # pkce、device_code 轮询、kimi、openai_codex 登录流程
└── utils/                   # 工具函数
    ├── env.py               # 按 provider 从环境变量读取 API key（映射与 TS 全量对齐）
    ├── provider_env.py      # provider 级 env 覆盖读取
    ├── error_body.py        # provider HTTP 错误标准化与格式化
    ├── estimate.py          # 上下文 token 估算（usage 锚点 + 字符估算）
    ├── model_utils.py       # calculate_cost、clamp/get_supported_thinking_levels 等
    ├── simple_options.py    # build_base_options、clamp_max_tokens_to_context
    ├── message_transformer.py # transform_messages（跨模型转换、孤儿工具调用补全）
    ├── json_parser.py       # 流式 JSON 片段解析（json-repair 封装）
    ├── surrogate.py         # Unicode 代理项清理
    ├── overflow.py          # 上下文溢出检测（多厂商错误模式）
    └── copilot.py           # GitHub Copilot 动态请求头构造
```

### 运行时架构要点

1. **Models 集合 + Provider 单元**  
   不存在全局注册表。`Models`（`gateway/models.py`）持有按 id 注册的 `Provider` 实例，负责鉴权解析（`getAuth`）、动态模型刷新（`refresh`）、登录/登出（`login`/`logout`）与 stream 派发（`_apply_auth` 合并 auth/model/options 的 apiKey、headers、env 后调用 `provider.stream*()`）。`stream*()` 同步返回事件流，auth 在后台异步解析，失败以 error 事件结束（`lazy_stream`）。Provider 找不到 API 实现时同样返回 error 流而非抛异常（StreamFunction 契约）。

2. **事件流模型**  
   所有流式调用返回 `AssistantMessageEventStream`（`streaming.py`）。生产者 `push(event)`，消费者 `async for` 迭代；收到 `DoneEvent` / `ErrorEvent` 时解析出最终 `AssistantMessage`，可用 `await stream.result()` 获取。`end()` 后不可再迭代。

3. **OpenAI Completions 实现**  
   当前唯一完整的协议实现位于 `api_impls/openai_completions.py`，使用官方 `openai.AsyncOpenAI` 客户端：
   - `detect_compat` / `get_compat` 按 provider/base_url 自动检测兼容标志（`store`、`developer` 角色、`reasoning_effort`、max_tokens 字段名、strict 模式等），`model.compat` 显式配置优先。
   - 流式 thinking/text 采用**双槽位合并**（整条流复用一个 thinking 块 + 一个 text 块，对齐 TS）。
   - reasoning 参数按 `thinking_format` 分派（openai / deepseek / zai / together / openrouter / ant-ling / qwen / chat-template / string-thinking），支持 `thinking_level_map` 映射与 `off` 显式 null 语义。
   - prompt 缓存：`prompt_cache_key` / `prompt_cache_retention: 24h` / Anthropic 风格 `cache_control` / DeepSeek 缓存统计。
   - Kimi deferred tools（toolResult 的 `added_tool_names` → system 消息携带工具定义）。
   - abort 通过看门狗任务（`signal.wait()` + `stream.close()`）实现与 TS fetch abort 同等的即时中断。

4. **消息转换与跨模型兼容**  
   `utils/message_transformer.py` 的 `transform_messages()` 两趟处理：思考块保留/转文本、跨模型删除 `thought_signature` 与工具调用 ID 规范化；为孤立工具调用插入合成 `ToolResultMessage`；跳过 `ERROR` / `ABORTED` 的不完整助手消息。重建消息一律用 `model_copy(update=...)` 保留未修改字段（如 `diagnostics`、`added_tool_names`）。

5. **序列化层约定（选型结果速查）**  
   选型依据遵循根 `AGENTS.md` 的"数据建模"决策顺序（可变性 → 序列化 → 校验价值 → 禁用项）。本包现有归类：
   - **Pydantic（`NovaBaseModel`）**：需要 JSON parse/dump 的类型——`Model`/`Usage`（models.json、会话 JSONL）、messages/content（会话 + RPC）、events（RPC 事件流）、compat（models.json）、`ApiKeyCredential`/`OAuthCredential`（auth.json，`OAuthCredential` 用 `extra="allow"` 保留扩展字段）、`ModelsStoreEntry`。
   - **dataclass**：纯代码构造、跨包传递的运行时容器，持 `Callable` 或服务实例——`StreamOptions` 家族（构造签名即契约，传错字段直接 `TypeError`）、`Provider`、`RefreshModelsContext`、`ApiKeyAuth`/`OAuthAuth`/`ProviderAuth`、`AuthResult`/`AuthCheck` 等。
   - **TypedDict / Protocol**：`ModelAuth`（camelCase dict 形状透传）、`AuthContext`/`CredentialStore`/`AuthInteraction`/`ProviderStreams`/`ModelsStore`。

6. **Auth 解析链**  
   `auth/resolve.py` 的优先级：调用方 apiKey override → 已存储 credential（OAuth 过期时在 store 锁内刷新，防并发双刷）→ 环境变量等 ambient 来源。`Models.get_auth(model)` 额外合并 model 静态 headers；`transform_headers` 选项（`StreamOptions.transform_headers`）在 headers 合并完成后、provider 派发前最后运行，且不会派发给 provider。

---

## 构建与开发命令

```bash
# 在 monorepo 根目录
pixi install -e dev

# 运行本包测试（排除真实 API 集成测试）
cd packages/nova_ai
pixi run -e dev pytest tests -m "not integration"

# 格式化
pixi run -e dev black src tests
pixi run -e dev isort src tests
```

---

## 代码风格指南

- **类名**：`PascalCase`；**函数 / 变量**：`snake_case`；**常量**：`UPPER_CASE`
- **导入排序**：`isort`（`profile = "black"`）；**格式化**：`black`（`py311`）
- **注释与文档字符串**：以**中文**为主
- **数据建模**：见上文"序列化层约定"与根 `AGENTS.md` 的"数据建模"决策顺序
- **与 TS 对齐**：本包结构/行为对齐 `pi/packages/ai`；修改语义前先看 TS 侧实现，注释中标注"对齐 TS xxx"

---

## 测试说明

测试位于 `tests/`：

```
tests/
├── unit/            # 类型、compat、estimate、overflow、transformer、stream、Models 集合等
├── providers/       # provider 工厂、动态刷新、stream 派发
├── auth/            # resolve、credential store、kimi/codex oauth、Credential schema
├── integration/     # 真实 API 集成测试（pytest.mark.integration，需 KIMI_API_KEY / VOLCENGINE_API_KEY）
└── fixtures/        # 测试图片等资源
```

运行方式：

```bash
cd packages/nova_ai
pixi run -e dev pytest tests -m "not integration"   # 跳过真实 API 调用
pixi run -e dev pytest tests                        # 全部（需相应 API key 环境变量）
```

---

## 安全注意事项

1. **API Key 来源**  
   本包不在磁盘持久化密钥（credential 持久化由上层注入的 `CredentialStore` 实现负责，如 nova_harness 的 `~/.nova/agent/auth.json`）。环境变量映射在 `utils/env.py`（与 TS `env-api-keys.ts` 全量对齐）；GitHub Copilot 依次尝试 `COPILOT_GITHUB_TOKEN`、`GH_TOKEN`、`GITHUB_TOKEN`；Anthropic 优先 `ANTHROPIC_OAUTH_TOKEN`。
2. **OAuth 流程**  
   `auth/oauth/` 实现 Kimi device code 与 OpenAI Codex（browser + device code）登录；回调页面模板在 `auth/oauth_page.py`。client_id 等常量硬编码在各自模块内，属公开客户端。
3. **字符串安全**  
   `utils/surrogate.py` 的 `sanitize_surrogates()` 在发送前移除未配对 Unicode 代理项，避免请求体解析失败。
4. **兼容检测的潜在风险**  
   `api_impls/openai_completions.py` 按 `base_url` 子串推断兼容标志（`volces.com`、`api.x.ai` 等）。URL 被恶意构造可能诱导不安全的参数回退；修改自动检测逻辑时保持谨慎。

---

## 开发惯例与给 AI Agent 的提示

- **先跑测试**：本包有完整非集成测试（`pixi run -e dev pytest tests -m "not integration"`），修改后必须通过。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文。
- **新增类型先想选型**：按根 `AGENTS.md` 的"数据建模"决策顺序（可变性 → 序列化 → 校验价值 → 禁用项）选定 `NovaBaseModel` / dataclass / TypedDict / Protocol。共享类型一律放 `types/`，不要在业务模块里重复定义。
- **新增 provider**：
  1. `providers/<name>/models.py` 写静态模型目录，`provider.py` 用 `create_provider()` + `env_api_key_auth()`（或自定义 auth）构造工厂；
  2. `providers/all.py` 加入 `builtin_providers()`；
  3. `utils/env.py` 补环境变量映射；
  4. `nova_ai/__init__.py` 与 `providers/__init__.py` 补导出。
- **新增 API 协议**：在 `api_impls/` 新建模块，导出 `stream` / `stream_simple`（模块即满足 `ProviderStreams` 契约），失败一律编码进返回的事件流而不是抛出。
- **修改消息类型需谨慎**：`types/messages.py` 与 `types/content.py` 是整个 monorepo 的公共契约。
- **流式事件不可复用**：`AssistantMessageEventStream` 收到 `DoneEvent`/`ErrorEvent` 或 `end()` 后队列即关闭。

---

## 版本与变更

- 当前版本：`0.1.0`（Alpha）
- 变更日志：`CHANGELOG.md`（当前为空）
