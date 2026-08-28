"""核心类型模块（契约层）

包含所有共享类型定义。分层规则：

- 本包只放**类型契约**（数据类、TypedDict、Protocol、别名），
  禁止运行时行为（工厂、I/O、流调度）——那些属于包根的运行时层
  （``nova_ai.models`` / ``nova_ai.streaming`` / ``nova_ai.signal``）。
- 依赖只允许指向 types/ 内部或包根叶子（``nova_ai.signal``），
  不得反向依赖运行时层。
"""

from .aliases import ProviderEnv, ProviderHeaders
from .auth import (
    ApiKeyAuth,
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthEvent,
    AuthInfoLink,
    AuthInteraction,
    AuthPrompt,
    AuthPromptOption,
    AuthResult,
    AuthType,
    Credential,
    CredentialInfo,
    CredentialStore,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)
from .base_model import NovaBaseModel
from .compat import (
    AnthropicMessagesCompat,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    VercelGatewayRouting,
)
from .content import ContentUnion, ImageContent, TextContent, ThinkingContent, ToolCall
from .enums import (
    Api,
    CacheRetention,
    KnownApi,
    KnownProvider,
    ModelThinkingLevel,
    ProviderId,
    StopReason,
    ThinkingFormat,
    ThinkingLevel,
    ThinkingLevelMap,
    Transport,
)
from .events import (
    AssistantMessageEvent,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from .messages import (
    AssistantMessage,
    Context,
    Message,
    Tool,
    ToolResultMessage,
    UserMessage,
)
from .model import Cost, Model, ModelCost, ModelCostRates, ModelCostTier, Usage
from .stream_options import (
    ProviderResponse,
    SimpleStreamOptions,
    StreamOptions,
    ThinkingBudgets,
)

__all__ = [
    # 基类
    "NovaBaseModel",
    # 枚举
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
    "ModelCostRates",
    "ModelCostTier",
    # 消息类型
    "AssistantMessage",
    "UserMessage",
    "ToolResultMessage",
    "Message",
    "Tool",
    "Context",
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
    "AnthropicMessagesCompat",
    "OpenAICompletionsCompat",
    "OpenAIResponsesCompat",
    "OpenRouterRouting",
    "VercelGatewayRouting",
    # 共享别名
    "ProviderEnv",
    "ProviderHeaders",
    # Auth 类型
    "ApiKeyAuth",
    "ApiKeyCredential",
    "AuthCheck",
    "AuthContext",
    "AuthEvent",
    "AuthInfoLink",
    "AuthInteraction",
    "AuthPrompt",
    "AuthPromptOption",
    "AuthResult",
    "AuthType",
    "Credential",
    "CredentialInfo",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
]
