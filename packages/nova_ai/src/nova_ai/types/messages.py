"""
核心消息类型定义
"""

from typing import List, Optional, Union, Literal, Dict, Any, TypeVar, Generic
from pydantic import Field
from .base_model import NovaBaseModel

from .enums import Api, Provider, StopReason
from .content import TextContent, ThinkingContent, ToolCall, ImageContent
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
    content: List[Union[TextContent, ThinkingContent, ToolCall]] = Field(default_factory=list)
    api: Api = ""                      # 使用的API类型
    provider: Provider = ""             # 服务提供商
    model: str = ""                      # 模型名称
    usage: Usage = Field(default_factory=Usage)  # 令牌使用统计
    stop_reason: StopReason = StopReason.STOP    # 停止原因
    error_message: Optional[str] = None           # 错误信息（如果有）
    response_id: Optional[str] = None             # 提供商返回的响应唯一标识符
    response_model: Optional[str] = None          # 提供商实际使用的模型名称（可能与请求不同）
    timestamp: int = 0                            # Unix时间戳（毫秒）


class ToolResultMessage(NovaBaseModel):
    """工具结果消息（用于上下文理解）"""
    role: Literal["toolResult"] = "toolResult"
    tool_call_id: str = ""
    tool_name: str = ""
    content: List[Union[TextContent, ImageContent]] = Field(default_factory=list)
    details: Optional[Dict[str, Any]] = None
    is_error: bool = False
    timestamp: int = 0


# 消息联合类型
Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


T = TypeVar('T')


class Tool(NovaBaseModel, Generic[T]):
    """工具定义"""
    name: str = ""
    description: str = ""
    parameters: Optional[T] = None  # 应该是 TypeBox TSchema 的对应物


class Context(NovaBaseModel):
    """上下文"""
    system_prompt: Optional[str] = None
    messages: List[Message] = Field(default_factory=list)
    tools: Optional[List[Tool]] = None
