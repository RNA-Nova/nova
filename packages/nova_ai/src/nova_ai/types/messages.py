"""
核心消息类型定义
"""

from typing import Annotated, Any, Dict, Generic, List, Literal, Optional, TypeVar, Union

from pydantic import ConfigDict, Field

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
    """助手消息。

    双重身份（AGENTS.md 数据建模规则 1 注记）：既是消息契约（规则 2 低频
    契约对象——随会话 JSONL / RPC 落盘出网），又是流式累积器（``_stream.py``
    在流式循环内原地改写 usage / stop_reason / content 等）——表示保留
    Pydantic 以满足边界 parse/dump 与规则 6 判别；赋值期校验关闭（校验发生
    在构造与解析边界，不跟每次增量写）。
    """

    model_config = ConfigDict(validate_assignment=False)

    role: Literal["assistant"] = "assistant"
    content: List[Annotated[Union[TextContent, ThinkingContent, ToolCall], Field(discriminator="type")]] = Field(
        default_factory=list
    )
    api: Api = "unknown"
    provider: ProviderId = "unknown"
    model: str = "unknown"
    usage: Usage = Field(default_factory=Usage)
    stop_reason: StopReason = StopReason.STOP
    # 提供商原始 finish_reason（未映射；对齐 TS rawStopReason）
    raw_stop_reason: Optional[str] = None
    # 提供商标记的回合自然结束（对齐 TS endTurn）
    end_turn: Optional[bool] = None
    # 提供商延迟完成标记（对齐 TS deferred）
    deferred: Optional[bool] = None
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
    content: List[Annotated[Union[TextContent, ImageContent], Field(discriminator="type")]] = Field(default_factory=list)
    # 任意 JSON 值（对齐 TS details: unknown）——工具自定义形态，渲染端按工具解释
    details: Optional[Any] = None
    is_error: bool = False
    # 本次工具调用新增注册的工具名列表（Kimi deferred tools 模式使用）
    added_tool_names: Optional[List[str]] = None
    timestamp: int = 0


# 消息联合类型（判别键 ``role``——规则 6：Context.messages 走 model_validate）
Message = Annotated[
    Union[UserMessage, AssistantMessage, ToolResultMessage],
    Field(discriminator="role"),
]


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
