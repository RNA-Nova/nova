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

# auth 行为层（类型词汇住 nova_protocol，本包不再再导出枢纽类型）
from .auth import (
    AuthResolutionOverrides,
    DefaultAuthContext,
    DeviceCodePollOptions,
    DeviceCodePollResult,
    DeviceCodePollStatus,
    InMemoryCredentialStore,
    ModelsError,
    ModelsErrorCode,
    PkceCodes,
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

# Models 集合 / Provider 运行时单元 / 模型目录存储
from .gateway import (
    InMemoryModelsStore,
    Models,
    ModelsStore,
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

# 重新导出 stream_options（AI 调用面参数包；类型词汇已迁 nova_protocol）
from .stream_options import (
    ProviderResponse,
    SimpleStreamOptions,
    StreamOptions,
    ThinkingBudgets,
)

# 重新导出 streaming 模块
from .streaming import (  # 事件流
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
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
    # auth
    "AuthResolutionOverrides",
    "DefaultAuthContext",
    "DeviceCodePollOptions",
    "DeviceCodePollResult",
    "DeviceCodePollStatus",
    "InMemoryCredentialStore",
    "ModelsError",
    "ModelsErrorCode",
    "PkceCodes",
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
