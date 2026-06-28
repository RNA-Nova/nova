"""
核心类型模块
包含所有基础数据类型定义
"""

from .base_model import NovaBaseModel
from .enums import (
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
)
from .content import TextContent, ThinkingContent, ToolCall, ImageContent, ContentUnion
from .model import Model, ModelCost, Usage, Cost
from .messages import (
    AssistantMessage,
    UserMessage,
    ToolResultMessage,
    Message,
    Tool,
    Context,
)
from .stream_options import (
    ThinkingBudgets,
    StreamOptions,
    SimpleStreamOptions,
    ProviderResponse,
)
from .events import (
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
)
from .api_adapter import ApiAdapter
from .compat import (
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    VercelGatewayRouting,
)

__all__ = [
    # 基类
    "NovaBaseModel",
    # 枚举
    "Api",
    "KnownApi",
    "Provider",
    "KnownProvider",
    "StopReason",
    "ThinkingLevel",
    "CacheRetention",
    "Transport",
    "ThinkingFormat",
    "ThinkingLevelMap",
    # 内容类型
    "TextContent",
    "ThinkingContent",
    "ToolCall",
    "ImageContent",
    "ContentUnion",
    # 使用统计
    "Usage",
    "Cost",
    # 模型类型
    "Model",
    "ModelCost",
    "Usage",
    "Cost",
    # 消息类型
    "AssistantMessage",
    "UserMessage",
    "ToolResultMessage",
    "Message",
    "Tool",
    "Context",
    # API 适配器
    "ApiAdapter",
    # 流选项
    "ThinkingBudgets",
    "StreamOptions",
    "SimpleStreamOptions",
    "ProviderResponse",
    # 事件类型
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
    # 兼容性配置
    "OpenAICompletionsCompat",
    "OpenAIResponsesCompat",
    "OpenRouterRouting",
    "VercelGatewayRouting",
]
