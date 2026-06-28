"""
Assistant Message 模块
提供助手消息相关的类型定义和事件流处理
"""

# 重新导出 types 模块
from .types import (
    # enums
    Api,
    KnownApi,
    Provider,
    KnownProvider,
    StopReason,
    ThinkingLevel,
    CacheRetention,
    Transport,
    ThinkingFormat,
    ThinkingLevelMap,
    # content
    TextContent,
    ThinkingContent,
    ToolCall,
    ImageContent,
    ContentUnion,
    # usage
    Usage,
    Cost,
    # messages
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
    Message,
    Tool,
    Context,
    # stream_options
    ThinkingBudgets,
    StreamOptions,
    SimpleStreamOptions,
    ProviderResponse,
    # events
    AssistantMessageEvent,
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolCallStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    DoneEvent,
    ErrorEvent,
    # compat
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    VercelGatewayRouting,
)

# 重新导出 streaming 模块
from .streaming import (
    # 事件流
    EventStream,
    AssistantMessageEventStream,
    create_assistant_message_event_stream,
    # 主要API函数
    stream,
    complete,
    stream_simple,
    complete_simple,
)

# 重新导出registry模块
from .registry import (
    # API注册表
    register_api_adapter,
    get_api_adapter,
    list_api_adapters,
    unregister_api_adapter,
    has_api_adapter,
    clear_api_adapters,
    # 模型注册表
    register_model,
    get_model,
    get_models_by_provider,
    list_providers,
    list_all_models,
    find_model_by_id,
    register_models_from_dict,
    # 内置注册
    register_builtin_api_adapters,
    register_builtin_models,
    register_all_builtins,
    reset_api_adapter_registry,
    reset_model_registry,
    reset_registry,
)

# 重新导出 apis 模块（API 协议实现）
from .api_impls import (
    # 各 API 协议的流式函数
    stream_openai_completions,
    stream_simple_openai_completions,
    OpenAICompletionsOptions,
    ProviderStreamOptions,
)

# 重新导出utils模块
from .utils import (
    # 环境变量
    get_env_api_key,
    get_env_api_key_typed,
    get_all_env_api_keys,
    # Copilot
    infer_copilot_initiator,
    has_copilot_vision_input,
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
    # JSON解析
    parse_streaming_json,
    # 字符串处理
    sanitize_surrogates,
    # 流选项
    build_base_options,
    clamp_reasoning,
    # 消息转换
    transform_messages,
    # 溢出检测
    is_context_overflow,
)

# 重新导出 models 模块（仅数据）
from .models import (
    Model,
    ModelCost,
    VOLCENGINE_MODELS,
    get_volcengine_model,
    list_volcengine_models,
)
from .utils import (
    calculate_cost,
    supports_xhigh_thinking,
    get_supported_thinking_levels,
)

# 注册所有内置组件
register_all_builtins()

__all__ = [
    # types.enums
    "Api",
    "KnownApi",
    "Provider",
    "KnownProvider",
    "StopReason",
    "ThinkingLevel",
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
    # streaming.invoke
    "stream",
    "complete",
    "stream_simple",
    "complete_simple",
    # types.api_adapter
    "ApiAdapter",
    # registry.api_registry
    "ApiRegistry",
    "register_api_adapter",
    "get_api_adapter",
    "list_api_adapters",
    "unregister_api_adapter",
    "has_api_adapter",
    "clear_api_adapters",
    # registry.model_registry
    "ModelRegistry",
    "register_model",
    "get_model",
    "get_models_by_provider",
    "list_providers",
    "list_all_models",
    "find_model_by_id",
    "register_models_from_dict",
    # registry.builtins
    "register_builtin_api_adapters",
    "register_builtin_models",
    "register_all_builtins",
    "reset_api_adapter_registry",
    "reset_model_registry",
    "reset_registry",
    # apis（API 协议实现）
    "stream_openai_completions",
    "stream_simple_openai_completions",
    "OpenAICompletionsOptions",
    "ProviderStreamOptions",
    # utils.env
    "get_env_api_key",
    "get_env_api_key_typed",
    "get_all_env_api_keys",
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
    # utils.stream_options
    "build_base_options",
    "clamp_reasoning",
    # utils.message_transformer
    "transform_messages",
    # utils.overflow
    "is_context_overflow",
    # types.compat
    "OpenAICompletionsCompat",
    "OpenAIResponsesCompat",
    "OpenRouterRouting",
    "VercelGatewayRouting",
    "ThinkingLevelMap",
    # utils.model_utils
    "calculate_cost",
    "supports_xhigh_thinking",
    "get_supported_thinking_levels",
    # models.data
    "VOLCENGINE_MODELS",
    "get_volcengine_model",
    "list_volcengine_models",
]
