"""
Assistant Message 模块
提供助手消息相关的类型定义和事件流处理
"""

# 重新导出core模块
from .core.enums import (
    Api, KnownApi, Provider, KnownProvider, StopReason,
    ThinkingLevel, CacheRetention, Transport, ThinkingFormat
)
from .core.content import TextContent, ThinkingContent, ToolCall, ImageContent
from .core.usage import Usage, Cost
from .core.messages import (
    UserMessage, AssistantMessage, ToolResultMessage,
    Message, MessageUnion, Tool, Context
)

# 重新导出streaming模块
from .streaming import (
    # 事件类型
    AssistantMessageEvent,
    StartEvent, TextStartEvent, TextDeltaEvent, TextEndEvent,
    ThinkingStartEvent, ThinkingDeltaEvent, ThinkingEndEvent,
    ToolCallStartEvent, ToolCallDeltaEvent, ToolCallEndEvent,
    DoneEvent, ErrorEvent,
    
    # 事件流
    EventStream, AssistantMessageEventStream,
    create_assistant_message_event_stream,
    
    # 主要API函数
    stream, complete, stream_simple, complete_simple,
)

# 重新导出registry模块
from .registry import (
    # API注册表
    ApiProvider, ApiProviderRecord, ApiProviderRegistry,
    register_api_provider, get_api_provider, list_api_providers,
    unregister_api_provider, has_api_provider, clear_api_providers,
    
    # 模型注册表
    ModelRegistry, ModelProvider,
    register_model, get_model, get_models_by_provider,
    list_providers, list_all_models, find_model_by_id,
    register_models_from_dict,
    
    # 内置注册
    register_builtin_api_providers, register_builtin_models,
    register_all_builtins,reset_api_registry,reset_model_registry, reset_registry,
)

# 重新导出providers模块
from .providers import (
    # 各个提供者的流式函数（可选）
    stream_openai_completions, stream_simple_openai_completions, OpenAICompletionsOptions
)

# 重新导出utils模块
from .utils import (
    # 环境变量
    get_env_api_key, get_env_api_key_typed, get_all_env_api_keys,
    
    # Copilot
    infer_copilot_initiator, has_copilot_vision_input,
    build_copilot_dynamic_headers, build_copilot_headers_from_messages,
    
    # JSON解析
    parse_streaming_json,
    
    # 字符串处理
    sanitize_surrogates,
    
    # 流选项
    build_base_options, clamp_reasoning, adjust_max_tokens_for_thinking,
    ThinkingBudgets, StreamOptions, SimpleStreamOptions,
    
    # 消息转换
    transform_messages,
    
    # HTTP代理
    setup_http_proxy, get_http_proxy, get_https_proxy,
    configure_http_client_proxy, is_node_environment,

    # overflow
    is_context_overflow
)

# 重新导出compat模块
from .compat import (
    OpenAICompletionsCompat, OpenAIResponsesCompat,
    OpenRouterRouting, VercelGatewayRouting
)

# 重新导出auth模块
from .auth import (
    has_vertex_adc_credentials, has_bedrock_credentials,
)

# 重新导出models模块（仅数据）
from .models import (
    Model, ModelCost,calculate_cost, supports_xhigh_thinking, models_are_equal,
    OPENAI_MODELS, ANTHROPIC_MODELS, GOOGLE_MODELS,
    get_openai_model, get_anthropic_model, get_google_model,
    list_openai_models, list_anthropic_models, list_google_models,
)

# 初始化
from .utils.http_proxy import setup_http_proxy
setup_http_proxy()

# 注册所有内置组件
from .registry import register_all_builtins
register_all_builtins()

__all__ = [
    # core.enums
    "Api", "KnownApi", "Provider", "KnownProvider", "StopReason",
    "ThinkingLevel", "CacheRetention", "Transport", "ThinkingFormat",
    
    # core.content
    "TextContent", "ThinkingContent", "ToolCall", "ImageContent",
    
    # core.usage
    "Usage", "Cost",
    
    # core.messages
    "UserMessage", "AssistantMessage", "ToolResultMessage",
    "Message", "MessageUnion", "Tool", "Context",
    
    # core.models
    "Model", "ModelCost",
    
    # streaming.events
    "AssistantMessageEvent",
    "StartEvent", "TextStartEvent", "TextDeltaEvent", "TextEndEvent",
    "ThinkingStartEvent", "ThinkingDeltaEvent", "ThinkingEndEvent",
    "ToolCallStartEvent", "ToolCallDeltaEvent", "ToolCallEndEvent",
    "DoneEvent", "ErrorEvent",
    
    # streaming.event_stream
    "EventStream", "AssistantMessageEventStream",
    "create_assistant_message_event_stream",
    
    # streaming.api
    "stream", "complete", "stream_simple", "complete_simple",
    
    # registry.api_registry
    "ApiProvider", "ApiProviderRecord", "ApiProviderRegistry",
    "register_api_provider", "get_api_provider", "list_api_providers",
    "unregister_api_provider", "has_api_provider", "clear_api_providers",
    
    # registry.model_registry
    "ModelRegistry", "ModelProvider",
    "register_model", "get_model", "get_models_by_provider",
    "list_providers", "list_all_models", "find_model_by_id",
    "register_models_from_dict",
    
    # registry.builtins
    "register_builtin_api_providers", "register_builtin_models",
    "register_all_builtins", "reset_api_registry",
    "reset_model_registry","reset_registry",
    
    # providers (可选)
    "stream_openai_completions", "stream_simple_openai_completions", "OpenAICompletionsOptions",
    
    # utils.env
    "get_env_api_key", "get_env_api_key_typed", "get_all_env_api_keys",
    
    # utils.copilot
    "infer_copilot_initiator", "has_copilot_vision_input",
    "build_copilot_dynamic_headers", "build_copilot_headers_from_messages",
    
    # utils.json_parser
    "parse_streaming_json",
    
    # utils.surrogate
    "sanitize_surrogates",
    
    # utils.stream_options
    "build_base_options", "clamp_reasoning", "adjust_max_tokens_for_thinking",
    "ThinkingBudgets", "StreamOptions", "SimpleStreamOptions",
    
    # utils.message_transformer
    "transform_messages",
    
    # utils.http_proxy
    "setup_http_proxy", "get_http_proxy", "get_https_proxy",
    "configure_http_client_proxy", "is_node_environment",

    # utils.overflow
    "is_context_overflow",
    
    # compat
    "OpenAICompletionsCompat", "OpenAIResponsesCompat",
    "OpenRouterRouting", "VercelGatewayRouting",
    
    # auth
    "has_vertex_adc_credentials", "has_bedrock_credentials",
    "is_vertex_fully_configured", "get_vertex_adc_path",
    "get_bedrock_credentials_type", "get_bedrock_region",
    
    # models.data
    "OPENAI_MODELS", "ANTHROPIC_MODELS", "GOOGLE_MODELS","calculate_cost", "supports_xhigh_thinking", "models_are_equal",
    "get_openai_model", "get_anthropic_model", "get_google_model",
    "list_openai_models", "list_anthropic_models", "list_google_models",
]