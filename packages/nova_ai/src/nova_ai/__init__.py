"""nova_ai —— 统一的 LLM 提供商抽象层

以 ``ModelsManager`` 集合 + ``Provider`` 运行时单元 + API 协议实现三层组织，
统一提供聊天、图像与分类调用；低层调用显式使用具体协议模块。架构对齐
TypeScript ``pi/packages/ai`` 的目标态。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api_impls import (
        AnthropicOptions,
        OpenAICompletionsOptions,
        ProviderStreamOptions,
    )
    from .auth import kimi_oauth, openai_codex_oauth

# 实现层共享件（轻，不含重依赖）
from .api_impls._shared import (
    build_base_options,
    build_copilot_dynamic_headers,
    has_copilot_vision_input,
    infer_copilot_initiator,
    transform_messages,
)

# auth 行为层（类型词汇住 nova_protocol，本包不再导出枢纽类型）
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
    lazy_oauth,
    oauth_error_html,
    oauth_success_html,
    poll_oauth_device_code_flow,
    resolve_provider_auth,
)

# ModelsManager 集合 / Provider 运行时单元 / 模型目录存储
from .gateway import (
    InMemoryModelsStore,
    ModelsManager,
    ModelsStore,
    Provider,
    ProviderClassifier,
    ProviderImages,
    ProviderStreams,
    RefreshModelsContext,
    create_models_manager,
    create_provider,
)

# 重新导出 providers 模块（内置 provider 工厂 + 模型数据）
from .providers import (
    ANTHROPIC_MODELS,
    KIMI_CODING_MODELS,
    MOONSHOTAI_CN_MODELS,
    MOONSHOTAI_MODELS,
    OPENROUTER_CLASSIFIER_MODELS,
    OPENROUTER_IMAGE_MODELS,
    VOLCENGINE_MODELS,
    anthropic_provider,
    builtin_models,
    builtin_providers,
    get_builtin_classifier_model,
    get_builtin_classifier_models,
    get_builtin_image_model,
    get_builtin_image_models,
    get_builtin_model,
    get_builtin_models,
    kimi_coding_provider,
    moonshotai_cn_provider,
    moonshotai_provider,
    openrouter_provider,
    volcengine_provider,
)

# 重新导出 stream_options（AI 调用面参数包；类型词汇已迁 nova_protocol）
from .stream_options import (
    ClassifierOptions,
    ImagesOptions,
    ModelIdentity,
    ProviderResponse,
    SimpleStreamOptions,
    StreamOptions,
    ThinkingBudgets,
)

# 重新导出 streaming 模块
from .streaming import EventStream  # 事件流
from .streaming import (
    AssistantMessageEventStream,
    create_assistant_message_event_stream,
)

# 重新导出 utils 模块（跨层通用件；实现层共享件从 api_impls._shared 再导出）
from .utils import (
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
    # streaming（streaming.py）
    "EventStream",
    "AssistantMessageEventStream",
    "create_assistant_message_event_stream",
    # gateway.models
    "ModelsManager",
    "create_models_manager",
    # gateway.store
    "InMemoryModelsStore",
    "ModelsStore",
    # apis（API 协议实现）
    "OpenAICompletionsOptions",
    "AnthropicOptions",
    "ProviderStreamOptions",
    # utils.env
    "get_env_api_key",
    # api_impls._shared.copilot_headers
    "infer_copilot_initiator",
    "has_copilot_vision_input",
    "build_copilot_dynamic_headers",
    # utils.json_parser
    "parse_streaming_json",
    # utils.surrogate
    "sanitize_surrogates",
    # stream_options
    "ThinkingBudgets",
    "StreamOptions",
    "SimpleStreamOptions",
    "ImagesOptions",
    "ClassifierOptions",
    "ProviderResponse",
    "ModelIdentity",
    # api_impls._shared.simple_options
    "build_base_options",
    # api_impls._shared.transform_messages
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
    "ANTHROPIC_MODELS",
    "anthropic_provider",
    "Provider",
    "ProviderClassifier",
    "ProviderImages",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_provider",
    "builtin_providers",
    "builtin_models",
    "get_builtin_classifier_model",
    "get_builtin_classifier_models",
    "get_builtin_image_model",
    "get_builtin_image_models",
    "get_builtin_model",
    "get_builtin_models",
    "kimi_coding_provider",
    "KIMI_CODING_MODELS",
    "moonshotai_provider",
    "MOONSHOTAI_MODELS",
    "moonshotai_cn_provider",
    "MOONSHOTAI_CN_MODELS",
    "openrouter_provider",
    "OPENROUTER_CLASSIFIER_MODELS",
    "OPENROUTER_IMAGE_MODELS",
    "volcengine_provider",
    "VOLCENGINE_MODELS",
]

# ---------------------------------------------------------------------------
# 惰性导出（PEP 562，对齐 TS subpath exports 的包体收益）：
# API 协议实现连带 openai SDK——只在真正访问这些名字时加载。
# ``from nova_ai import OpenAICompletionsOptions`` 等既有用法零改动。
# ---------------------------------------------------------------------------


def __getattr__(name: str):
    if name in (
        "OpenAICompletionsOptions",
        "AnthropicOptions",
        "ProviderStreamOptions",
    ):
        from . import api_impls as _api_impls

        value = getattr(_api_impls, name)
        globals()[name] = value
        return value
    if name in ("kimi_oauth", "openai_codex_oauth"):
        from . import auth as _auth

        value = getattr(_auth, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> "list[str]":
    return sorted(
        set(globals())
        | {
            "OpenAICompletionsOptions",
            "AnthropicOptions",
            "ProviderStreamOptions",
            "kimi_oauth",
            "openai_codex_oauth",
        }
    )
