"""Nova Gateway 的 SSE 事件判别联合（partial 不上线）。"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from nova_protocol import NovaBaseModel, NovaErrorKind, ToolCall, Usage
from pydantic import Field, StrictInt, TypeAdapter


class GatewayStart(NovaBaseModel):
    """助手响应开始。"""

    type: Literal["start"] = "start"


class GatewayTextStart(NovaBaseModel):
    """指定内容块的文本开始。"""

    type: Literal["text_start"] = "text_start"
    content_index: StrictInt = Field(ge=0)


class GatewayTextDelta(NovaBaseModel):
    """指定内容块的文本增量。"""

    type: Literal["text_delta"] = "text_delta"
    content_index: StrictInt = Field(ge=0)
    delta: str


class GatewayTextEnd(NovaBaseModel):
    """文本块结束；签名缺失表示 Provider 未提供。"""

    type: Literal["text_end"] = "text_end"
    content_index: StrictInt = Field(ge=0)
    content_signature: Optional[str] = None


class GatewayThinkingStart(NovaBaseModel):
    """指定内容块的思考开始。"""

    type: Literal["thinking_start"] = "thinking_start"
    content_index: StrictInt = Field(ge=0)


class GatewayThinkingDelta(NovaBaseModel):
    """指定内容块的思考增量。"""

    type: Literal["thinking_delta"] = "thinking_delta"
    content_index: StrictInt = Field(ge=0)
    delta: str


class GatewayThinkingEnd(NovaBaseModel):
    """思考块结束；签名缺失表示 Provider 未提供。"""

    type: Literal["thinking_end"] = "thinking_end"
    content_index: StrictInt = Field(ge=0)
    content_signature: Optional[str] = None


class GatewayToolCallStart(NovaBaseModel):
    """工具调用开始，携带调用 ID 与工具名。"""

    type: Literal["toolcall_start"] = "toolcall_start"
    content_index: StrictInt = Field(ge=0)
    id: str
    tool_name: str


class GatewayToolCallDelta(NovaBaseModel):
    """工具参数 JSON 的文本增量。"""

    type: Literal["toolcall_delta"] = "toolcall_delta"
    content_index: StrictInt = Field(ge=0)
    delta: str


class GatewayToolCallEnd(NovaBaseModel):
    """工具调用结束，携带完整调用值。"""

    type: Literal["toolcall_end"] = "toolcall_end"
    content_index: StrictInt = Field(ge=0)
    tool_call: ToolCall


class GatewayDone(NovaBaseModel):
    """正常完成；思考档缺失表示 Provider 未回报。"""

    type: Literal["done"] = "done"
    reason: Literal["stop", "length", "toolUse"]
    usage: Usage
    provider_thinking_level: Optional[str] = None


class GatewayError(NovaBaseModel):
    """错误或取消终态；可选诊断字段缺失时出货省略。"""

    type: Literal["error"] = "error"
    reason: Literal["aborted", "error"]
    usage: Usage
    error_message: Optional[str] = None
    error_kind: Optional[NovaErrorKind] = None
    retry_after_ms: Optional[int] = None
    provider_thinking_level: Optional[str] = None


GatewayAssistantMessageEvent = Annotated[
    Union[
        GatewayStart,
        GatewayTextStart,
        GatewayTextDelta,
        GatewayTextEnd,
        GatewayThinkingStart,
        GatewayThinkingDelta,
        GatewayThinkingEnd,
        GatewayToolCallStart,
        GatewayToolCallDelta,
        GatewayToolCallEnd,
        GatewayDone,
        GatewayError,
    ],
    Field(discriminator="type"),
]
GATEWAY_EVENT_ADAPTER = TypeAdapter(GatewayAssistantMessageEvent)

__all__ = [
    "GATEWAY_EVENT_ADAPTER",
    "GatewayAssistantMessageEvent",
    "GatewayDone",
    "GatewayError",
    "GatewayStart",
    "GatewayTextDelta",
    "GatewayTextEnd",
    "GatewayTextStart",
    "GatewayThinkingDelta",
    "GatewayThinkingEnd",
    "GatewayThinkingStart",
    "GatewayToolCallDelta",
    "GatewayToolCallEnd",
    "GatewayToolCallStart",
]
