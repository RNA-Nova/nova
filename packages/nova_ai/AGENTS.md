<!-- AGENTS.md - nova_ai 包级指南 -->

# nova_ai —— 统一的 LLM 提供商抽象层

> 本文件面向 AI Coding Agent 编写。如果你不了解 `nova_ai` 这个子包，请从这里开始阅读。

## 项目概览

`nova_ai` 是 Nova monorepo 的最底层子包，职责是为上层（`nova_agent`、`nova_harness` 等）提供**多厂商统一的流式调用接口**。架构与 TypeScript 端 `pi/packages/ai` 对齐：以 `ModelsManager` 集合（TS 侧名 `Models`——改名消歧：集合名与 `Provider.models` 字段同词）+ `Provider` 运行时单元 + API 协议实现（`api_impls/`）三层组织，对外暴露 `stream` / `complete` / `stream_simple` / `complete_simple` API。

- **源码包名**：`nova_ai`
- **版本**：`0.1.5`
- **目标语言**：Python `>=3.13,<3.14`
- **项目语言**：代码注释与文档主要使用**中文**
- **License**：MIT
- **作者**：Liujinming

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python `>=3.13,<3.14` |
| 包管理器 | **pixi**（monorepo 根目录统一 workspace；Poetry 配置保留为兼容） |
| 格式化 | `black`（目标语法版本 `py313`） |
| Import 排序 | `isort`（`profile = "black"`） |
| 数据建模 | `pydantic` v2 + `dataclass`（按根 `AGENTS.md` 决策顺序选型） |
| 异步运行时 | `asyncio` |
| 开发依赖 | `pytest`、`pytest-asyncio` |
| 关键运行时依赖 | `openai >= 2.0, < 3.10.0`（3.10.0 引入流式收尾回归，暂避，上游修复后放开）、`anthropic >= 1.0`（稳定线为界——httpx2 底座 1.x 全程，0.x pre-stable 不支持）、`pydantic >= 2.0`、`json-repair >= 0.58.4`、`httpx`、`anyio >= 4.11`（CancelScope 桥）；兄弟包 `nova-protocol`/`nova-async` 为运行时硬依赖（需同装） |

**未使用** Mypy、Tox、Makefile、Docker。monorepo 根的 GitHub Actions（`.github/workflows/ci.yml`）现有四腿：`executor-rust` / `executor-py` / `coding-agent-py` / `blob-size`——本包经 `coding-agent-py` 腿覆盖（ubuntu 单平台；CI 仅 nova_protocol/nova_async/nova-exec-server-client 以 editable 安装，其余兄弟包非 editable）；尚无独立的 nova_ai 测试腿与三平台矩阵。

---

## 代码组织与模块划分

源码位于 `src/nova_ai/`；**构建期模型目录管线已独立成包 `nova_catalog`**
（`packages/nova_catalog`，对位 pi `packages/ai/catalog/`——自包含构建期
世界：知识归 `providers/` 各家定义、
族事实归 `families/`、薄拉取归 `sources/`、词汇归 `define.py`、引擎归
`build.py`、入口归 `generate.py`；不依赖 nova_ai 运行时，类型经
nova_protocol 共用）：

```
src/nova_ai/
├── __init__.py              # 本包门面（行为层 / gateway / providers / stream_options / utils；
│                            #   跨组件类型词汇一律住 nova_protocol，本包不再导出）
├── stream_options.py        # StreamOptions、SimpleStreamOptions、ThinkingBudgets、ProviderResponse、
│                            #   ModelIdentity（dataclass；钩子面模型归因白名单摘要）
├── streaming.py             # EventStream、AssistantMessageEventStream（异步迭代器 + result Future）
├── model_catalog.py         # 模型目录运行时机器：flatten_model_catalog（数据分片拍平）+
│                            #   finalize_catalog_models（运行时盖戳单点）+
│                            #   builtin_catalog_generated_at（新鲜度竞速锚点）；
│                            #   修正引擎单实现在 nova_catalog.engine（构建/运行同一台），
│                            #   知识归 nova_catalog.providers 各 DEF——工厂装配时挂
│                            #   Provider.catalog_knowledge 送达（无注册表无查找；
│                            #   provider 独有裁决归各家回调）
├── models_generated.py      # 生成的模型目录聚合器
├── testkit/                 # 跨包共享契约测试设施（store_contracts；各包 tests/contracts/ 实例化）
├── gateway/                 # ModelsManager 网关层（对齐 TS models.ts + models-store.ts）
│   ├── models.py            # ModelsManager 集合 + create_models_manager（auth 网关 + provider 注册表 +
│   │                        #   刷新世代 + 目录站容器路由——CatalogServices 单站/dict 双形态，
│   │                        #   按 provider.catalog_source 路由 + partial 绑定发放 ctx.catalog_fetch +
│   │                        #   on_diagnostic 诊断实时缝——组合根注入，refresh 诊断双通道外发）
│   ├── provider.py          # Provider / create_provider（模型目录宿主 + 协议路由 +
│   │                        #   ModelsPublication 发布契约 + merge_models 单点 +
│   │                        #   replace 读取位（personalized 形态）；装配链：
│   │                        #   personalize → personalized 策略；catalog_source →
│   │                        #   overlay 策略出生自带；皆无 → 静态（裸 refresh_models
│   │                        #   口袋自定义，pi 同款））
│   ├── strategies.py        # 刷新策略工厂（治理唯一实现——恢复/拉站/分流/
│   │                        #   finalize/persist+publish 只此一份）：
│   │                        #   overlay_refresh_policy（纯站六件套：isNewer 守卫 +
│   │                        #   4h TTL + ETag/304/404·501/瞬时分流）+
│   │                        #   personalized_refresh_policy（跳 TTL/ETag，
│   │                        #   finalize 后调 personalize 钩子，replace 读取）
│   │                        #   ——pi withRemoteCatalog/withPersonalizedCatalog 对位
│   ├── catalog.py           # 目录契约词汇（CatalogOutcome / CatalogService 站契约 /
│   │                        #   CatalogServices 容器形状 / FetchCatalogFn；
│   │                        #   ModelFields 家族已迁 nova_protocol——管线与运行期共引）
│   ├── streams.py           # lazy_stream（ProviderStreams 契约原语：同步签名 + 异步装配 + 错误即流终态）
│   └── store.py             # ModelsStore / InMemory 实现
│                            #   （落盘 schema ModelsStoreEntry 住 nova_protocol）
├── api_impls/               # API 协议实现
│   ├── _shared/             # 实现层共享件（transform_messages / simple_options / retry /
│   │                        #   prompt_cache / copilot_headers / user_agent / timeouts /
│   │                        #   transcript——转录折叠机：normalize/resolve/get_current_tools/
│   │                        #   resolve_transcript_tools 三模式分工，pi transcript.ts 对位）
│   ├── openai_completions/  # OpenAI Completions（模块即满足 ProviderStreams）
│   │                        #   compat / messages / client / options / params / reasoning / _stream 分件
│   │                        #   + moonshot_schema.py（moonshot-flavored 子集规范化——
│   │                        #   anyOf 子项补型，nova 特有厂商收窄）
│   └── anthropic_messages/  # Anthropic Messages（官方 anthropic SDK，httpx2 底座）
│                            #   options / compat / client / params / messages / _stream 分件
│                            #   + constrained.py（strict 工具按工具裁决 + schema 净化 +
│                            #   限量降级词表——pi constrained-sampling.ts 对位）
│                            #   + threads.py（message threads 服务端续接——台账/指纹/
│                            #   建线-续接-拒回退三态，pi a758f3ae4 对位）
├── session_resources.py     # 会话级资源清理注册处（threads 台账等——pi
│                            #   session-resources.ts 对位）
├── catalog_impls/           # 目录站策略枚举（CatalogService 内置实现，对位 api_impls/）
│   ├── models_dev.py        # ModelsDevCatalog（v2 单体目录切片 + 实例级 60s 缓存——
│   │                        #   翻译委托 nova_catalog.sources.models_dev，全仓单点）
│   └── pi_dev.py            # PiDevCatalog（per-provider 目录 + 真条件请求 ETag/304）
├── providers/               # 内置 provider 工厂与静态模型目录（纯装配层：数据 + 工厂 + 知识 + 私活）
│   ├── all.py               # builtin_providers() / builtin_models()
│   ├── anthropic/           # models.py + provider.py（pi.dev 全供，零运行时 corrections——
│   │                        #   种子知识归 nova_catalog/providers/）
│   ├── kimi_coding/         # models.py + provider.py + catalog.py（personalize 钩子：
│   │                        #   managed 订阅源拉取+翻译+覆盖式合并——名单+活字段，
│   │                        #   未收录跳过+诊断；账户层零修正；openai-completions 协议）
│   ├── moonshotai/          # models.py + provider.py（知识全归 nova_catalog DEF；
│   │                        #   运行时零修正文件）
│   ├── moonshotai_cn/       # 同上（DEF 共享 moonshotai 定义件）
│   └── volcengine/          # models.py + provider.py + catalog.py（personalize 钩子：
│                            #   ark 在售状态拉取+筛选——行为与旧回调一致）
├── auth/                    # 鉴权行为层（类型词汇住 nova_protocol.auth）
│   ├── resolve.py           # resolve_provider_auth + ModelsError（ModelsErrorCode
│   │                        #   六域 code + nova_error_kind 自声明）+ AuthResolutionOverrides
│   ├── credential_store.py  # InMemoryCredentialStore（同步引擎；写互斥归编排层 per-provider 锁）
│   ├── context.py           # DefaultAuthContext（env / 文件存在性）
│   ├── helpers.py           # env_api_key_auth / lazy_oauth
│   └── oauth/               # pkce、device_code 轮询、oauth_page（回调成功/失败 HTML）、
│                            #   kimi、openai_codex 登录流程
└── utils/                   # 跨层通用工具函数（实现层共享件归 api_impls/_shared）
    ├── env.py               # 按 provider 从环境变量读取 API key（映射与 TS 全量对齐）
    ├── provider_env.py      # provider 级 env 覆盖读取
    ├── http.py              # HTTP 薄封装原语：request_with_abort（async 在飞中断）
    │                        #   + fetch_json（GET+重试+非 200 raise）+ json_headers
    ├── errors.py            # 错误归一枢纽（normalize_error 六级阶梯 + NormalizedError
    │                        #   + provider 错误体标准化，对位 codex error.rs）
    ├── estimate.py          # 上下文 token 估算（usage 锚点 + 字符估算）
    ├── model_utils.py       # calculate_cost、clamp/get_supported_thinking_levels 等
    ├── json_parser.py       # 流式 JSON 片段解析（json-repair 封装 + 增量解析器）
    ├── surrogate.py         # Unicode 代理项清理
    ├── overflow.py          # 上下文溢出检测（多厂商错误模式）
    ├── diagnostics.py       # append_diagnostic（AssistantMessage 诊断追加单点——
    │                        #   threads 回退/strict 降级留痕，pi diagnostics.ts 对位）
    └── hash.py              # short_hash（与 TS 位级对齐的确定性短哈希）
```

> 注：取消/等待机制件（`AbortScope` / `await_with_abort` / `await_with_signal` / `abortable_sleep` / `any_signal` / `operation_signal`）、`TaskTracker`（属主级在途任务台账，codex/tokio-util 对位）、`KeyedLockFactory` 编排锁协议（原 `auth/refresh_locks.py`——文件锁 adapter 仍住 nova_harness）、`Deadline` 预算值对象（原 `gateway/deadline.py`——90s 缺省策略件留 `gateway/models.py`）与 `backoff_delay_ms` 退避序列已按结构标准 §二升格出口迁住 `nova_async`（机制族叶子包），本包经 `from nova_async import ...` 消费；取消词汇（`AbortSignal` 等）锚定 `nova_protocol` 不动。

### 运行时架构要点

1. **ModelsManager 集合 + Provider 单元**  
   不存在全局注册表。`ModelsManager`（`gateway/models.py`）持有按 id 注册的 `Provider` 实例，负责鉴权解析（`getAuth`）、动态模型刷新（`refresh`）、登录/登出（`login`/`logout`）与 stream 派发（`_apply_auth` 合并 auth/model/options 的 apiKey、headers、env 后调用 `provider.stream*()`）。`stream*()` 同步返回事件流，auth 在后台异步解析，失败以 error 事件结束（`lazy_stream`）。Provider 找不到 API 实现时同样返回 error 流而非抛异常（StreamFunction 契约）。动态性为能力字段：`Provider.refresh_models` 为 None 即静态，注入策略闭包即动态——**刷新治理只有一份**（`gateway/strategies.py` 的策略工厂，两形态：overlay 纯站六件套 / personalized 站+账户钩子，pi `withRemoteCatalog`/`withPersonalizedCatalog` 对位），`create_provider` 装配链：`personalize` → personalized 策略（+replace 读取位）；仅 `catalog_source` → overlay 策略出生自带；皆无 → 静态（要自定义刷新就实现 `refresh_models` 裸口袋，pi 同款）。目录站为运行时共享资源：manager 持有 `CatalogServices` 容器（单站/dict 双形态，对位 `ApiImpl`），按 `Provider.catalog_source` 路由并经 `partial(service.fetch, provider.id)` 绑定后发放进 `RefreshModelsContext.catalog_fetch`——refresh 实现的可选能力；kimi/火山的私有鉴权拉取（managed 订阅源、ark 在售筛选）是各家目录的 `personalize` 钩子（装配参数，类型无痕），凭据与公开站都经 context 获取。

2. **事件流模型**  
   所有流式调用返回 `AssistantMessageEventStream`（`streaming.py`）。生产者 `push(event)`，消费者 `async for` 迭代；收到 `DoneEvent` / `ErrorEvent` 时解析出最终 `AssistantMessage`，可用 `await stream.result()` 获取。`end()` 后不可再迭代。

3. **协议实现（当前两个）**  
   `api_impls/openai_completions/`（官方 `openai.AsyncOpenAI`）与 `api_impls/anthropic_messages/`（官方 `anthropic.AsyncAnthropic`，httpx2 底座）——模块即满足 `ProviderStreams` 契约；分件同构（compat / messages / client / params / _stream，openai 另有 reasoning）。openai_completions 的实现要点：
   - `detect_compat` / `get_compat` 按 provider/base_url 自动检测兼容标志（`store`、`developer` 角色、`reasoning_effort`、max_tokens 字段名、strict 模式等），`model.compat` 显式配置优先。
   - 流式 thinking/text 采用**双槽位合并**（整条流复用一个 thinking 块 + 一个 text 块，对齐 TS）。
   - reasoning 参数按 `thinking_format` 分派（openai / deepseek / zai / together / openrouter / ant-ling / qwen / qwen-chat-template / chat-template / baseten / string-thinking），支持 `thinking_level_map` 映射与 `off` 显式 null 语义。
   - prompt 缓存：`prompt_cache_key` / `prompt_cache_retention: 24h` / Anthropic 风格 `cache_control` / DeepSeek 缓存统计。
   - Kimi 工具新增（`resolve_transcript_tools` additions 模式：中途 SystemMessage 的 `tools_added` 经 kimi 工具型 system 消息就地锚定，顶层列表取最后一次非增量变更后的状态）。
   - abort 经 `AbortScope`（anyio CancelScope 的桥）实现与 TS fetch abort 同等的即时真杀——signal 触发即取消 scope，块内一切等待（connect→TTFB→逐事件）真死。

   anthropic_messages 的实现要点：
   - **INLINE_TOOLS**（`inline-tools-2026-09-15`）：`supports_mid_convo_system_messages` + `supports_mid_convo_tool_changes` 双旗标且首条 system 消息携带初始工具时，顶层工具列表固定为"初始工具 + 常驻占位（`DEFERRED_TOOL_PLACEHOLDER`）"，后续工具变更以 `tool_addition`/`tool_removal` 内容块随中途 system 消息按值下发——缓存前缀存活（pi b271b0a52 起的形态；旧的 defer_loading/tool_reference 机制已随 pi 拆除）。
   - **message threads**（`message-threads-2026-08-12`，`threads.py`）：session id + 缓存开启时只发增量消息续接上一条回复——台账按会话记（前缀数 + 设置/消息指纹 + reply id），续接被拒换新线程全量重发一次，端点不认则不带 `thread` 重发并会话级关停；`StreamOptions.disable_continuation` 供缓存预热类重放；会话终结经 `session_resources.cleanup_session_resources` 释放。
   - **mid-convo effort**：effort 标记逐条注入（历史档回放 + 当前档收尾），顶层 `output_config.effort` 固定 `low`（组织 cap 账户拒高档——pi e3bc05e87）；`block_binding.drop_block` 归 thinking-binding-controls beta。
   - **strict 工具按工具裁决** + **限量降级环**（`constrained.py`）：prefer-strict 工具被 400 限量（词表三句）时整请求降级非严格重发一次，留 `anthropic_strict_tools_fallback` 诊断。
   - **服务端 refusal fallback**（`server-side-fallback-2026-07-01`）：`compat.allowed_fallback_models`（≤3，带本地定价——cost 住 `nova_protocol/cost.py` 叶子模块，compat↔model 不成环）→ `params.fallbacks` + beta 头；fallback 应答按 fallback 费率计成本，`fallback` 标记块只许在输出开头。
   - SDK 出货边界：`create()` 只收声明命名参数——声明集外的请求字段（`thread`/`fallbacks`/`temperature` 等）统归 `extra_body` 上线（`_stream._send` 单点）。

4. **消息转换与跨模型兼容**  
   `api_impls/_shared/transform_messages.py` 的 `transform_messages()` 两趟处理：思考块保留/转文本、跨模型删除 `thought_signature` 与工具调用 ID 规范化；为孤立工具调用插入合成 `ToolResultMessage`；跳过 `ERROR` / `ABORTED` 的不完整助手消息。重建消息一律用 `model_copy(update=...)` 保留未修改字段（如 `diagnostics`）。

5. **序列化层约定（选型结果速查）**  
   选型依据遵循根 `AGENTS.md` 的"数据建模"决策顺序（可变性 → 序列化 → 校验价值 → 禁用项），住所规则见 `docs/crosscutting/data-modeling.md`。本包现有归类：
   - **Pydantic（`NovaBaseModel`）**：需要 JSON parse/dump 的类型——全部住 `nova_protocol` 枢纽：`Model`/`Usage`（models.json、会话 JSONL）、messages/content（会话 + RPC）、events（RPC 事件流）、compat（models.json）、`ApiKeyCredential`/`OAuthCredential`（auth.json，`OAuthCredential` 用 `extra="allow"` 保留扩展字段）、`ModelsStoreEntry`（models-store.json）。
   - **dataclass**：纯代码构造、跨包传递的运行时容器，持 `Callable` 或服务实例——`StreamOptions` 家族（构造签名即契约，传错字段直接 `TypeError`）、`Provider`、`RefreshModelsContext`、`ModelsPublication`、`ApiKeyAuth`/`OAuthAuth`/`ProviderAuth`、`AuthResult`/`AuthCheck` 等（不可变值对象一律 `frozen=True, kw_only=True`，如 `CatalogOutcome`）。
   - **TypedDict / Protocol**：`ModelAuth` / `ApiKeyAuthInput`（dict 形状透传——声明不校验）、`ModelFields`/`ModelCostFields`（目录字段包形状，`Model(**fields)` 在目录边界一次性校验）、`AuthContext`/`CredentialStore`/`AuthInteraction`/`ProviderStreams`/`ModelsStore`/`CatalogService`（公开目录站契约；`CatalogServices` 容器形状与 `FetchCatalogFn` 为签名别名）。

6. **Auth 解析链**  
   `auth/resolve.py` 的优先级：调用方 apiKey override → 已存储 credential（OAuth 过期时在 per-provider 刷新锁内复查→刷新→写，防并发双刷）→ 环境变量等 ambient 来源。`ModelsManager.get_auth(model)` 额外合并 model 静态 headers；`transform_headers` 选项（`StreamOptions.transform_headers`）在 headers 合并完成后、provider 派发前最后运行，且不会派发给 provider。

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
- **导入排序**：`isort`（`profile = "black"`）；**格式化**：`black`（`py313`）
- **注释与文档字符串**：以**中文**为主
- **数据建模**：见上文"序列化层约定"与根 `AGENTS.md` 的"数据建模"决策顺序
- **与 TS 对齐**：本包结构/行为对齐 `pi/packages/ai`；修改语义前先看 TS 侧实现，注释中标注"对齐 TS xxx"

---

## 测试说明

测试位于 `tests/`（组织细则见根 `docs/crosscutting/testing-modeling.md` 第一节——目录镜像 src、命名与密度条款）：

```
tests/
├── gateway/         # ModelsManager 集合 / Provider 单元 / 刷新机器 / 刷新策略
│                    #   （test_strategies.py：overlay 六件套 + personalized 钩子形态）/ 站路由
├── catalog_impls/   # 公开站实现（pi_dev / models_dev）+ HTTP 级用例（respx）
├── api_impls/       # 协议实现（openai_completions / anthropic_messages 各切面 + _shared）
├── auth/            # 鉴权链（resolve / credential store / helpers；oauth/ 子包同构）
├── providers/       # 内置 provider（kimi_coding/ volcengine/ 子目录同构 + 跨 provider）
├── utils/           # 工具函数（json_parser / http / env / errors 等；abort 测试随机制件迁 nova_async）
├── contracts/       # Protocol 接缝契约套件（非镜像，条款三.5）
├── _helpers/        # 跨文件共享设施：fake_llm.py（假 LLM 服务器——请求捕获 +
│                    #   双方言 SSE 构造器，codex wiremock 对位，见 testing-modeling §四.3）
├── integration/     # 特性契约 e2e（一特性一文件 + 探针矩阵（每 provider 一个最便宜
│                    #   模型）+ `_helpers/` 凭据链/选型/提取件；`integration` 标记 +
│                    #   enable_socket 解禁 + timeout 300s——默认套件密封不跑；
│                    #   全模型可用性巡检归 nova_catalog/scripts/check_live_providers.py）
├── fixtures/        # 测试图片等资源
└── （根级）          # src 根模块（streaming / model_catalog / stream_options）+ 包级 meta（imports /
                     #   task_hygiene / placement 住所审计 / examples 保鲜 / model_data_freshness 门禁）
```

运行方式：

```bash
cd packages/nova_ai
pixi run -e dev pytest tests -m "not integration"   # 默认套件（密封，零网络）
pixi run -e dev test-integration                    # 特性契约 e2e（真实 API，用 ~/.nova/agent/auth.json 与 env 凭据）
pixi run -e dev check-live                          # 全模型可用性巡检（运维面，报告制）
```

---

## 安全注意事项

1. **API Key 来源**  
   本包不在磁盘持久化密钥（credential 持久化由上层注入的 `CredentialStore` 实现负责，如 nova_harness 的 `~/.nova/agent/auth.json`）。环境变量映射在 `utils/env.py`（与 TS `env-api-keys.ts` 全量对齐）；GitHub Copilot 依次尝试 `COPILOT_GITHUB_TOKEN`、`GH_TOKEN`、`GITHUB_TOKEN`；Anthropic 优先 `ANTHROPIC_OAUTH_TOKEN`。
2. **OAuth 流程**  
   `auth/oauth/` 实现 Kimi device code 与 OpenAI Codex（browser + device code）登录；回调页面模板在 `auth/oauth/oauth_page.py`。client_id 等常量硬编码在各自模块内，属公开客户端。
3. **字符串安全**  
   `utils/surrogate.py` 的 `sanitize_surrogates()` 在发送前移除未配对 Unicode 代理项，避免请求体解析失败。
4. **兼容检测的潜在风险**  
   `api_impls/openai_completions/compat.py` 按 `base_url` 子串推断兼容标志（`volces.com`、`api.x.ai` 等）。URL 被恶意构造可能诱导不安全的参数回退；修改自动检测逻辑时保持谨慎。

---

## 开发惯例与给 AI Agent 的提示

- **先跑测试**：本包有完整非集成测试（`pixi run -e dev pytest tests -m "not integration"`），修改后必须通过。
- **保持中文注释**：新增代码的 docstring 与行内注释请使用中文。
- **新增类型先想选址与选型**：跨组件边界的纯数据词汇进 `nova_protocol` 枢纽（消费方一律 `from nova_protocol import X`，本包不中转再导出）；组件内部类型留本包。表示选型按根 `AGENTS.md` 的"数据建模"决策顺序（可变性 → 序列化 → 校验价值 → 禁用项）选定 `NovaBaseModel` / dataclass / TypedDict / Protocol。
- **新增 provider**：
  1. `providers/<name>/models.py` 写静态模型目录，`provider.py` 用 `create_provider()` + `env_api_key_auth()`（或自定义 auth）构造工厂；
  2. `providers/all.py` 加入 `builtin_providers()`；
  3. `utils/env.py` 补环境变量映射；
  4. `nova_ai/__init__.py` 与 `providers/__init__.py` 补导出。
- **新增 API 协议**：在 `api_impls/` 新建模块，导出 `stream` / `stream_simple`（模块即满足 `ProviderStreams` 契约），失败一律编码进返回的事件流而不是抛出。
- **修改消息类型需谨慎**：`nova_protocol` 的 `messages.py` 与 `content.py` 是整个 monorepo 的公共契约。
- **流式事件不可复用**：`AssistantMessageEventStream` 收到 `DoneEvent`/`ErrorEvent` 或 `end()` 后队列即关闭。

---

## 版本与变更

- 当前版本：`0.1.5`（Alpha）
- 变更日志：`CHANGELOG.md`（已记 0.1.0 封版）
