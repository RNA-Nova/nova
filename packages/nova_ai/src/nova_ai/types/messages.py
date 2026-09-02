"""
核心消息类型定义
"""

from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import Field

from .base_model import NovaBaseModel
from .content import ImageContent, TextContent, ThinkingContent, ToolCall
from .enums import Api, ProviderId, StopReason
from .model import Usage


class UserMessage(NovaBaseModel):
    """用户消息（用于上下文理解）"""

    role: Literal["user"] = "user"
    content: Union[str, List[Union[TextContent, ImageContent]]] = Field(
        default_factory=list,
    )
    timestamp: int = 0


class AssistantMessage(NovaBaseModel):
    """助手消息"""

    role: Literal["assistant"] = "assistant"
    content: List[Union[TextContent, ThinkingContent, ToolCall]] = Field(
        default_factory=list
    )
    api: Api = "unknown"
    provider: ProviderId = "unknown"
    model: str = "unknown"
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = StopReason.STOP
    error_message: Optional[str] = None
    response_id: Optional[str] = None
    response_model: Optional[str] = None
    # Redacted provider/runtime diagnostics for failures and recoveries.
    diagnostics: Optional[List[Dict[str, Any]]] = None
    timestamp: int = 0


class ToolResultMessage(NovaBaseModel):
    """工具结果消息（用于上下文理解）"""

    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: List[Union[TextContent, ImageContent]] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    is_error: bool = False
    # 本次工具调用新增注册的工具名列表（Kimi deferred tools 模式使用）
    added_tool_names: Optional[List[str]] = None
    timestamp: int = 0


# 消息联合类型
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


T = TypeVar("T")


class Tool(NovaBaseModel, Generic[T]):
    """工具定义"""

    name: str
    description: str
    parameters: T


class Context(NovaBaseModel):
    """上下文"""

    system_prompt: Optional[str] = None
    messages: List[Message] = Field(default_factory=list)
    tools: Optional[List[Tool]] = None
