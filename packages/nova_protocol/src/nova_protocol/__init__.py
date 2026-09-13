"""nova_protocol —— Nova 词汇枢纽。

跨组件边界的全部纯数据形状（零行为、零兄弟包依赖）。收录标准、三纪律与
序列化出口约定见包 README。

消费方一律 ``from nova_protocol import X``（本模块全量门面再导出，
内部文件布局自由）。
"""

from .aliases import ProviderEnv, ProviderHeaders
from .auth import (
    ApiKeyAuth,
    ApiKeyAuthInput,
    ApiKeyCredential,
    AuthCheck,
    AuthContext,
    AuthEvent,
    AuthInfoLink,
    AuthInteraction,
    AuthorizationRequest,
    AuthPrompt,
    AuthPromptOption,
    AuthResult,
    AuthType,
    Credential,
    CredentialInfo,
    CredentialStore,
    LoginCancelledError,
    ModelAuth,
    OAuthAuth,
    OAuthCredential,
    ProviderAuth,
)
from .base_model import NovaBaseModel
from .compat import (
    AnthropicMessagesCompat,
    DeferredToolsMode,
    OpenAICompletionsCompat,
    OpenAIResponsesCompat,
    OpenRouterRouting,
    SessionAffinityFormat,
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
    ThinkingTokenBudgetField,
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
from .ids import EntryId, RunId, SessionId, ToolCallId
from .messages import (
    AssistantMessage,
    Context,
    Message,
    Tool,
    ToolResultMessage,
    UserMessage,
)
from .model import (
    Cost,
    Model,
    ModelCost,
    ModelCostRates,
    ModelCostTier,
    ModelsStoreEntry,
    Usage,
)
from .signal import AbortController, AbortedError, AbortSignal

__all__ = [
    # 基类
    "NovaBaseModel",
    # 语义 id
    "SessionId",
    "EntryId",
    "RunId",
    "ToolCallId",
    # 取消原语
    "AbortController",
    "AbortSignal",
    "AbortedError",
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
    "ThinkingTokenBudgetField",
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
    "ModelsStoreEntry",
    # 消息类型
    "AssistantMessage",
    "UserMessage",
    "ToolResultMessage",
    "Message",
    "Tool",
    "Context",
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
    "SessionAffinityFormat",
    "DeferredToolsMode",
    # 共享别名
    "ProviderEnv",
    "ProviderHeaders",
    # Auth 类型
    "ApiKeyAuth",
    "ApiKeyAuthInput",
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
    "AuthorizationRequest",
    "Credential",
    "LoginCancelledError",
    "CredentialInfo",
    "CredentialStore",
    "ModelAuth",
    "OAuthAuth",
    "OAuthCredential",
    "ProviderAuth",
]
