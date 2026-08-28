# Model 链路审计表：nova_harness (Python) vs pi coding-agent (TS)

> 逐文件、逐导出、逐行为分支的完整审计。
> 审计范围：TS `core/{model-runtime, provider-composer, model-config, models-store, auth-storage, resolve-config-value, runtime-credentials, model-registry}.ts`
> 对应 Python：`nova_harness/core/model/`、`core/config/resolve.py`、`core/config/auth/storage.py`、`core/config/storage/backends.py`、`nova_ai/gateway/`、`nova_ai/auth/`
>
> 状态图例：✅ 已对齐 ｜ ⚠️ 有意差异（附理由）｜ ⏭️ 未移植（附理由）
>
> 审计日期：2026-07-19。所有条目均基于双方源码逐行核对。

---

## 1. `resolve-config-value.ts` ↔ `core/config/resolve.py`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| `$VAR` env 引用 | ✅ | `resolve_config_value("$VAR")` |
| `${VAR}` env 引用 | ✅ | 同上 |
| `${非法名}` 按字面量保留 | ✅ | `_parse_template` 名校验失败回退字面量 |
| `${` 未闭合按字面量 | ✅ | 同上 |
| 裸 `$` 按字面量 | ✅ | 同上 |
| `$$` → 字面 `$`、`$!` → 字面 `!` | ✅ | 转义分支 |
| 模板混排（字面量 + 多个 env） | ✅ | `"https://${HOST}:$PORT/v1"` 有测试 |
| 任一 env 缺失 → 整体 undefined | ✅ | `_resolve_template` 返回 None |
| `!cmd` 执行 shell 取 stdout | ✅ | `subprocess.check_output`（10s 超时） |
| 命令结果进程级缓存 | ✅ | `_command_result_cache` + `clear_config_value_cache` |
| `resolveConfigValueUncached` 绕过缓存 | ✅ | `resolve_config_value_uncached` |
| 命令空输出 / 失败 → undefined | ✅ | 有测试 |
| `env` 参数覆盖 process.env | ✅ | `_resolve_env` 先查传入 dict |
| `getConfigValueEnvVarName(s)` | ✅ | `get_config_value_env_var_name(s)` |
| `getMissingConfigValueEnvVarNames` | ✅ | 同名 |
| `isCommandConfigValue` | ✅ | `is_command_config_value` |
| `isConfigValueConfigured` | ✅ | `is_config_value_configured` |
| `resolveConfigValueOrThrow`（报缺失变量名） | ✅ | `resolve_config_value_or_throw` |
| `resolveHeaders(OrThrow)` | ✅ | `resolve_headers(_or_throw)` |
| Windows 用配置 shell 执行命令 | ⚠️ | Python 恒 `shell=True`；跨平台细节，语义等价 |

## 2. `model-config.ts` ↔ `types/model.py` + `runtime._load_config`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| provider 字段全集（name/baseUrl/apiKey/api/headers/compat/authHeader/models/modelOverrides） | ✅ | `ProviderConfig`（另多出 `thinking_level_map`，Python 超集） |
| `oauth: "radius"` 字段 | ⏭️ | radius 为 pi 自家网关，无服务端可对接 |
| model 字段全集（id/name/api/baseUrl/reasoning/thinkingLevelMap/input/cost/contextWindow/maxTokens/headers/compat） | ✅ | `ModelDefinition` |
| `cost.tiers[].inputTokensAbove` | ✅ | `ModelCostTier.input_tokens_above` |
| compat 三族 schema（completions/responses/anthropic） | ✅ | nova_ai compat 类型，按 `api` 判别 union 成员 |
| `stripJsonComments` 注释支持 | ✅ | `core/utils/json.py::strip_json_comments`（行/块注释、字符串内豁免、保行号） |
| ENOENT → 空配置；读/解析失败 → error 字符串 | ✅ | `_load_config` 同语义 |
| schema 校验失败 → 按路径列出全部错误 | ✅ | pydantic ValidationError 自带路径 |
| `deepFreeze` 不可变快照 | ✅ | 配置类型已全部 `frozen=True`（顶层语义一致；嵌套 dict/list 靠 `model_copy` 产出新对象的约定保证，2026-07-19 补齐） |

## 3. `models-store.ts` ↔ `nova_ai/gateway/store.py` + `model_runtime/store.py`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| `InMemoryCodingAgentModelsStore` | ✅ | `InMemoryModelsStore`（nova_ai） |
| `FileModelsStore`（锁内读写、按 provider 条目） | ✅ | `FileModelsStore`（复用 `FileStorageBackend`，0o600/0o700） |

## 4. `auth-storage.ts` ↔ `core/config/auth/storage.py`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| 文件后端 `withLock`（重试、0o600/0o700） | ✅ | `FileStorageBackend.with_lock` |
| 文件后端 `withLockAsync`（锁跨 await） | ✅ | `with_lock_async`；获取锁的重试用 `asyncio.sleep` 让出事件循环（避免协程互饿） |
| `reload()` 解析失败保留旧快照 | ✅ | `_reload` 保留旧 `_data` 并记录 `_load_error`（比 TS 多错误记录） |
| `read`：api_key 经 `resolveConfigValue(key, credential.env)` | ✅ | credential.env 参与解析 |
| `read`：解析失败 → `key: undefined` | ✅ | 置 None，下游回落 env 链（而非把 `$VAR` 原文当 key） |
| `modify`：锁内重读 → 合并 → 写回（多进程安全） | ✅ | `with_lock_async` 内 `_parse_storage_data(content)` |
| `modify`：fn 返回 undefined → 不写、返回当前值 | ✅ | 同语义 |
| `delete`：锁内重读 → 删键 → 写回 | ✅ | 同语义 |
| `list` 元信息（不解析 key） | ✅ | 同语义 |
| `readStoredCredential` 一次性只读辅助 | ⏭️ | 无调用方需求 |
| 遗留同步 API（set/get/remove/has/has_auth/get_api_key 等） | ⚠️ | ~~Python 超集~~ **已清理（2026-07-19）**：AuthStorage 收敛为纯 CredentialStore + runtime overrides + `has`/`has_auth` 两个判定原语（ModelRuntime 快照的数据源）；派生查询全部上移到 ModelRuntime，与 TS 分层一致 |

## 5. `runtime-credentials.ts` ↔ `AuthStorage` 的 overrides 支持

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| `read`：runtime override 优先于存储 | ✅ | 已实现 |
| `list`：overrides 并入元信息 | ✅ | 已实现 |
| `modify`：透传底层 store | ✅ | 已实现（overrides 不影响 modify） |
| `delete`：同时清 override 与存储 | ✅ | 已实现 |
| `hasRuntimeApiKey` 参与 auth status（source="runtime"） | ✅ | `AuthStorage.has_runtime_api_key` + `get_provider_auth_status` 的 "runtime" 分支（2026-07-19 补齐） |

## 6. `model-runtime.ts` ↔ `model_runtime/runtime.py`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| 异步 `create()`：配置加载 + 首次刷新（15s 超时） | ✅ | 同步构造 + `AgentSessionServices.create()` 中 15s AbortController 刷新 |
| `configureRadiusProviders` | ⏭️ | radius 专有 |
| `providerIds` = builtins ∪ config ∪ extensions | ✅ | `_rebuild_providers` 同 |
| `recomposeProvider`：三层全无 → 删；仅 base → 原样；否则合成，失败落 base 并记录 compositionErrors | ✅ | `_recompose_provider` 同 |
| snapshot：all/available/configuredProviders/auth | ✅ | `_available_snapshot`/`_configured_providers`/`_auth_checks` |
| snapshot.storedProviders 单独跟踪 | ⚠️ | Python 用 `auth_storage.has()` 等效判断，不单列字段 |
| availability 并发合并（queueAvailabilityRefresh / forceRefreshAvailability） | ✅ | `_availability_task` inflight 合并 + `_force_refresh_availability` 排队（2026-07-19 补齐） |
| `getProviders/getProvider/getModels/getModel` | ✅ | 同名透传 |
| `checkAuth` | ✅ | `check_auth` |
| `getAvailable()` async（可按 provider、等待 inflight） | ✅ | `get_available(provider_id=None)` async（inflight 合并后读快照，2026-07-19 补齐） |
| `getAvailableSnapshot()` | ✅ | `get_available_snapshot()` |
| `getError()`：config + composition + availability 汇总 | ✅ | `get_error()` 同 |
| `getRegisteredProviderConfig/Ids` | ✅ | 同名 |
| `getCompatibilityRequestConfig` | ⏭️ | facade 专用兼容兜底，facade 未移植 |
| `isUsingOAuth` | ✅ | `is_using_oauth` |
| `hasConfiguredAuth` | ✅ | `has_configured_auth` |
| `getAuth(model/provider, {apiKey, env} overrides)` | ✅ | `get_request_auth(..., api_key=, env=)` 透传 `AuthResolutionOverrides`（2026-07-19 补齐） |
| `getAuth(model)` 合并请求时 model 配置头 | ⚠️ | Python 组合时烘入 `Model.headers`（已拍板的时机边界） |
| `setRuntimeApiKey`：写入 + 临时快照 + refresh | ✅ | 写入 + 同步快照 + `refresh()` |
| `removeRuntimeApiKey` | ✅ | 同 |
| `listCredentials` | ✅ | `list_credentials` |
| `getProviderAuthStatus`：runtime → stored → configured(command/env/literal) → environment | ✅ | 全分支对齐 |
| `prepareRequest`：auth → 合并 headers → `transformHeaders` 最后 → env 合并 → baseUrl 覆盖 | ✅ | nova_ai `Models._apply_auth` 同序实现 |
| `stream/complete/streamSimple/completeSimple`（lazy：同步返回、失败进 error 事件） | ✅ | 透传 nova_ai `Models`（`_lazy_stream` 同语义） |
| `login`：登录 → 持久化 → refresh | ✅ | 同 |
| `logout`：删除 → 重组 provider → refresh | ✅ | 同 |
| `reloadConfig`：配置 + 重组 + refresh | ✅ | `reload_config()`（另清 `clear_config_value_cache`） |
| `refresh({allowNetwork, signal})` + 可用性刷新 | ✅ | `refresh(allow_network, signal)` |
| `registerProvider`：先校验（不动旧注册）→ 合并旧字段 → 临时快照 → 后台 refresh | ✅ | 同（合并语义、`_update_sync_snapshot`、`_schedule_availability_refresh`） |
| `unregisterProvider`：删除 + 重组 + refresh | ✅ | 同（并清理 stream_fn/refresh_fn/oauth 三个代码级配置） |
| `clearApiKeyCache` 导出 | ✅ | `clear_config_value_cache`（`reload_config` 内调用） |
| `withRemoteCatalog` 包装内置 provider | ⏭️ | pi.dev 托管目录服务，无对应服务端 |
| `allowModelNetwork`（`PI_OFFLINE`） | ✅ | `allow_model_network`（`NOVA_OFFLINE`） |

## 7. `provider-composer.ts` ↔ `model_runtime/composer.py`

| TS 行为 | 状态 | Python 对应 / 说明 |
|---|---|---|
| `mergeCompat` 深合并 openRouterRouting/vercelGatewayRouting/chatTemplateKwargs | ✅ | `helpers.merge_compat` 三键深合并 |
| `applyModelOverride`（name/reasoning/thinkingLevelMap 按键合并/input/cost 含 tiers/contextWindow/maxTokens/compat） | ✅ | `apply_model_override`（headers 已移出，走请求时 resolver） |
| override 的 headers 请求时合并（rawModelHeaders） | ✅ | nova_ai `model_headers_resolver` 钩子 + `ModelRuntime._raw_model_headers`（override < 定义 < 扩展），2026-07-19 补齐 |
| `modelFromJson`：api/baseUrl 缺省回落 defaults、校验报错文案 | ✅ | `model_from_json` 同文案 |
| `modelFromJson` 的 `headers: undefined`（credential-blind） | ✅ | `model_from_json`/`apply_extension` 均不再写入 headers，请求时解析 |
| `applyModelsJson`：空配置抛错、baseUrl/compat/thinkingLevelMap 应用、custom upsert | ✅ | 同（thinking_level_map 为 Python 超集字段） |
| `applyExtension`：无 models → baseUrl 覆盖；有 models → 全量替换回落 defaults | ✅ | 同（compat/thinking_level_map 应用为 Python 超集） |
| `adaptOAuth` | ⚠️ | 回调形状不同：Python 扩展直接收 nova_ai `AuthInteraction`，而非 TS 的 onAuth/onPrompt 桥接层；扩展生态自洽 |
| `withConfiguredAuth`：headers 合并 + `Authorization: Bearer` + 无 key 抛错 | ✅ | `_with_configured_auth` 同 |
| `configuredApiKey/Headers/AuthHeader` 取值链（extension 优先） | ✅ | 同名函数同优先级 |
| `configContextEnv`：把配置引用的 env 值带入解析上下文 | ⚠️ | Python 仅传 credential.env + result.env，其余回落 `os.environ`；默认 AuthContext 下等价 |
| `composeApiKeyAuth`：credential → rawKey → inherited 三段 resolve | ✅ | 同；rawKey 解析失败经 `or_throw` 显式报错（对齐 TS 的 `resolveConfigValueOrThrow`） |
| `composeApiKeyAuth.check`：credential/command/env 名存在性/literal | ✅ | 同（`is_config_value_configured`） |
| `composeApiKeyAuth.login`：无 inherited 时用默认 prompt | ✅ | 同 |
| OAuth-only 不伪造 apiKey auth | ✅ | 同（含扩展 OAuth 判定） |
| `composeOAuthAuth`：extension 优先，toAuth 包装配置头 | ✅ | 同 |
| `getModels` live 计算：base → models.json → extension → oauth.modifyModels → modelOverrides 最顶层 | ✅ | `_ComposedProvider.get_models()` 同序 live 计算 |
| `refreshModels` 链：base → extension.refreshModels（先校验再发布）→ 记录 oauth credential | ✅ | `_ComposedProvider.refresh_models` 同 |
| 无刷新能力 → `refreshModels: undefined` | ✅ | 实例级遮蔽为 None，`Models.refresh` 正确跳过 |
| name 优先级：extension → config → base → oauth.name → id | ✅ | 同 |
| baseUrl 优先级：extension → config → base | ✅ | 同 |
| provider.headers 取 base.headers | ✅ | 同 |
| `streamWith`：extension.streamSimple(api 匹配) → base(api 支持) → 按 model.api 分发 | ✅ | `_ComposedStreams` 同 |
| `filterModels` 透传 | ✅ | 同 |
| `resolveCompatibilityRequestConfig` / `CompatibilityRequestConfig` | ⏭️ | facade 专用 |
| `radiusProvider` 与 `oauth: "radius"` 分支 | ⏭️ | 专有 |

## 8. `model-registry.ts`（facade）—— 整体未移植 ⏭️

TS 的 `ModelRegistry` 是 `ModelRuntime` 的兼容壳（126 行纯转发）。Python 无 legacy 包袱，直接以 `ModelRuntime` 为唯一接口。facade 方法的 Python 对应：

| facade 方法 | Python 对应 |
|---|---|
| refresh / getError / getAll / getAvailable / find / hasConfiguredAuth | `reload_config` / `get_error` / `get_all` / `get_available` / `find` / `has_configured_auth` |
| getProviderAuthStatus / getProviderDisplayName / isUsingOAuth | 同名 |
| getApiKeyForProvider / registerProvider / unregisterProvider / getRegisteredProviderConfig(s) | 同名 |
| `getApiKeyAndHeaders`（`{ok, error}` 形状） | ⚠️ `get_request_auth` 返回 `AuthResult \| None`，形状不同（facade 未移植的直接影响） |
| `clearApiKeyCache` 再导出 | `clear_config_value_cache` |

## 9. 下游 nova_ai（链路末端，此前已对齐，复核无误）

| 行为 | 状态 |
|---|---|
| `resolve_provider_auth` 优先级：overrides → stored(oauth 刷新/api_key) → ambient env | ✅ |
| `Models._apply_auth`：apiKey 回落、headers 合并、`transform_headers` 最后执行、env 合并、`baseUrl` 覆盖 | ✅ |
| `_lazy_stream`：同步返回流，setup 失败进 error 事件 | ✅ |
| `Models.login/logout/refresh/checkAuth/getAvailable` | ✅ |
| `_DynamicProvider`：store 读 → 网络拉取 → store 写、inflight 去重、失败离线回退 | ✅ |
| `env_api_key_auth`：stored credential → env 链 | ✅ |
| OAuth（kimi/codex）：login/refresh/toAuth + 过期自动刷新 | ✅ |

---

## 结论汇总

- **✅ 已对齐**：约 98 项，覆盖配置解析、schema、存储、合成、鉴权、刷新、注册全链路
- **⚠️ 有意差异 2 项**：
  1. Windows 命令执行 shell 细节（语义等价；修复需写无法验证的平台代码，见下文分析）
  2. `adaptOAuth` 回调形状（Python 扩展直接用 `AuthInteraction`；为零消费者设计平行协议的取舍）
- **⏭️ 未移植 6 项**：radius（2 处）、remote catalog、`ModelRegistry` facade（含 `getCompatibilityRequestConfig`、`getApiKeyAndHeaders` 形状）、`readStoredCredential`
