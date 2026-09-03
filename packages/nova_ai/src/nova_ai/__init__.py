"""nova_ai —— 统一的 LLM 提供商抽象层

以 ``Models`` 集合 + ``Provider`` 运行时单元 + API 协议实现三层组织，
对外暴露一致的 ``stream`` / ``complete`` / ``stream_simple`` /
``complete_simple`` API。架构对齐 TypeScript ``pi/packages/ai``。
"""

# 实现层共享件（轻，不含重依赖）
from .api_impls._shared import (
    build_base_options,
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
    has_copilot_vision_input,
    infer_copilot_initiator,
    transform_messages,
)

# 重新导出 auth 模块（行为层；类型定义在 .types）
from .auth import (
    AuthResolutionOverrides,
    DefaultAuthContext,
    DeviceCodePollOptions,
    DeviceCodePollResult,
    InMemoryCredentialStore,
    ModelsError,
    ModelsErrorCode,
    default_provider_auth_context,
    env_api_key_auth,
    generate_pkce,
    kimi_oauth,
    lazy_oauth,
    oauth_error_html,
    oauth_success_html,
    openai_codex_oauth,
    poll_oauth_device_code_flow,
    resolve_provider_auth,
)

# 重新导出 ModelsStore
# 重新导出 Models 集合
# 重新导出 Models 集合与 Provider 运行时单元
from .gateway import (
    InMemoryModelsStore,
    Models,
    ModelsStore,
    ModelsStoreEntry,
    Provider,
    ProviderStreams,
    RefreshModelsContext,
    create_models,
    create_provider,
)

# 重新导出 providers 模块（内置 provider 工厂 + 模型数据）
from .providers import (
    KIMI_CODING_MODELS,
    MOONSHOTAI_CN_MODELS,
    MOONSHOTAI_MODELS,
    VOLCENGINE_MODELS,
    builtin_models,
    builtin_providers,
    get_builtin_model,
    get_builtin_models,
    get_kimi_coding_model,
    get_moonshotai_cn_model,
    get_moonshotai_model,
    get_volcengine_model,
    kimi_coding_provider,
    list_kimi_coding_models,
    list_moonshotai_cn_models,
    list_moonshotai_models,
    list_volcengine_models,
    moonshotai_cn_provider,
    moonshotai_provider,
    volcengine_provider,
)

# 重新导出 signal 模块
from .signal import AbortController, AbortSignal

# 重新导出 streaming 模块
from .streaming import (  # 事件流
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
)

# 重新导出 types 模块
from .types import (  # enums; content; usage; messages; stream_options; events; compat; auth
    AnthropicMessagesCompat,
    Api,
    ApiKeyAuth,
    ApiKeyCredential,
    AssistantMessage,
    AssistantMessageEvent,
    AuthCheck,
    AuthContext,
    AuthEvent,
    AuthInfoLink,
    AuthInteraction,
    AuthPrompt,
    AuthPromptOption,
    AuthResult,
    AuthType,
    CacheRetention,
    ContentUnion,
    Context,
    Cost,
    Credential,
    CredentialInfo,
    CredentialStore,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    KnownApi,
    KnownProvider,
    Message,
    Model,
    ModelAuth,
    ModelCost,
    ModelCostRates,
    ModelCostTier,
    ModelThinkingLevel,
    OAuthAuth,
    OAuthCredential,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    ProviderAuth,
    ProviderEnv,
    ProviderHeaders,
    ProviderId,
    ProviderResponse,
    SimpleStreamOptions,
    StartEvent,
    StopReason,
    StreamOptions,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingBudgets,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingFormat,
    ThinkingLevel,
    ThinkingLevelMap,
    ThinkingStartEvent,
    Tool,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultMessage,
    Transport,
    Usage,
    UserMessage,
    VercelGatewayRouting,
)

# 重新导出utils模块（跨层通用件；实现层共享件从 api_impls._shared 再导出）
from .utils import (  # 环境变量; JSON解析; 字符串处理; 溢出检测
    calculate_cost,
    clamp_thinking_level,
    get_env_api_key,
    get_supported_thinking_levels,
    has_api,
    is_context_overflow,
    models_are_equal,
    parse_streaming_json,
    sanitize_surrogates,
    to_thinking_level,
)

__all__ = [
    # signal
    "AbortController",
    "AbortSignal",
    # auth
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthCheck",
    "AuthContext",
    "AuthEvent",
    "AuthInfoLink",
    "AuthInteraction",
    "AuthPrompt",
    "AuthPromptOption",
    "AuthResolutionOverrides",
    "AuthResult",
    "AuthType",
    "Credential",
    "CredentialInfo",
    "CredentialStore",
    "DefaultAuthContext",
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "InMemoryCredentialStore",
    "ModelAuth",
    "ModelsError",
    "ModelsErrorCode",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
    "ProviderEnv",
    "ProviderHeaders",
    "default_provider_auth_context",
    "env_api_key_auth",
    "generate_pkce",
    "kimi_oauth",
    "lazy_oauth",
    "oauth_success_html",
    "oauth_error_html",
    "openai_codex_oauth",
    "poll_oauth_device_code_flow",
    "resolve_provider_auth",
    # types.enums
    "Api",
    "KnownApi",
    "ProviderId",
    "KnownProvider",
    "StopReason",
    "ThinkingLevel",
    "ModelThinkingLevel",
    "CacheRetention",
    "Transport",
    "ThinkingFormat",
    # types.content
    "TextContent",
    "ThinkingContent",
    "ToolCall",
    "ImageContent",
    "ContentUnion",
    # types.usage
    "Usage",
    "Cost",
    # types.messages
    "UserMessage",
    "AssistantMessage",
    "ToolResultMessage",
    "Message",
    "Tool",
    "Context",
    # types.models
    "Model",
    "ModelCost",
    "ModelCostRates",
    "ModelCostTier",
    # types.events
    "AssistantMessageEvent",
    "StartEvent",
    "TextStartEvent",
    "TextDeltaEvent",
    "TextEndEvent",
    "ThinkingStartEvent",
    "ThinkingDeltaEvent",
    "ThinkingEndEvent",
    "ToolCallStartEvent",
    "ToolCallDeltaEvent",
    "ToolCallEndEvent",
    "DoneEvent",
    "ErrorEvent",
    # streaming.event_stream
    "EventStream",
    "AssistantMessageEventStream",
    "create_assistant_message_event_stream",
    # nova_ai.models
    "Models",
    "create_models",
    # nova_ai.models_store
    "InMemoryModelsStore",
    "ModelsStore",
    "ModelsStoreEntry",
    # apis（API 协议实现）
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
    # utils.env
    "get_env_api_key",
    # utils.copilot
    "infer_copilot_initiator",
    "has_copilot_vision_input",
    "build_copilot_dynamic_headers",
    "build_copilot_headers_from_messages",
    # utils.json_parser
    "parse_streaming_json",
    # utils.surrogate
    "sanitize_surrogates",
    # types.stream_options
    "ThinkingBudgets",
    "StreamOptions",
    "SimpleStreamOptions",
    "ProviderResponse",
    # utils.simple_options
    "build_base_options",
    # utils.message_transformer
    "transform_messages",
    # utils.overflow
    "is_context_overflow",
    # types.compat
    "AnthropicMessagesCompat",
    "OpenAICompletionsCompat",
    "OpenAIResponsesCompat",
    "OpenRouterRouting",
    "VercelGatewayRouting",
    "ThinkingLevelMap",
    # utils.model_utils
    "calculate_cost",
    "clamp_thinking_level",
    "get_supported_thinking_levels",
    "to_thinking_level",
    "has_api",
    "models_are_equal",
    # providers
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_provider",
    "builtin_providers",
    "builtin_models",
    "get_builtin_model",
    "get_builtin_models",
    "kimi_coding_provider",
    "KIMI_CODING_MODELS",
    "get_kimi_coding_model",
    "list_kimi_coding_models",
    "moonshotai_provider",
    "MOONSHOTAI_MODELS",
    "get_moonshotai_model",
    "list_moonshotai_models",
    "moonshotai_cn_provider",
    "MOONSHOTAI_CN_MODELS",
    "get_moonshotai_cn_model",
    "list_moonshotai_cn_models",
    "volcengine_provider",
    "VOLCENGINE_MODELS",
    "get_volcengine_model",
    "list_volcengine_models",
]


# ---------------------------------------------------------------------------
# 惰性导出（PEP 562，对齐 TS subpath exports 的包体收益）：
# API 协议实现连带 openai SDK——只在真正访问这些名字时加载。
# ``from nova_ai import OpenAICompletionsOptions`` 等既有用法零改动。
# ---------------------------------------------------------------------------


def __getattr__(name: str):
    if name in ("OpenAICompletionsOptions", "ProviderStreamOptions"):
        from . import api_impls as _api_impls

        value = getattr(_api_impls, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> "list[str]":
    return sorted(
        set(globals()) | {"OpenAICompletionsOptions", "ProviderStreamOptions"}
    )
