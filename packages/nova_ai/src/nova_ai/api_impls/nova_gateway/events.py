"""线上事件 → 标准 AssistantMessageEvent 的 partial 重建。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional, cast

from nova_gateway_protocol import (
    GatewayAssistantMessageEvent,
    GatewayDone,
    GatewayError,
    GatewayStart,
    GatewayTextDelta,
    GatewayTextEnd,
    GatewayTextStart,
    GatewayThinkingDelta,
    GatewayThinkingEnd,
    GatewayThinkingStart,
    GatewayToolCallDelta,
    GatewayToolCallEnd,
    GatewayToolCallStart,
)
from nova_protocol import (
    AssistantMessage,
    AssistantMessageEvent,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    StopReason,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    Usage,
)

from nova_ai import StreamingJsonParser


def new_partial(model: Model) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        stop_reason=StopReason.PENDING,
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=Usage(),
        timestamp=int(time.time() * 1000),
    )


def _set_block(partial: AssistantMessage, index: int, block: Any) -> None:
    if index < 0 or index > len(partial.content):
        raise ValueError(f"Invalid proxy contentIndex: {index}")
    if index == len(partial.content):
        partial.content.append(block)
    else:
        partial.content[index] = block


def _block_at(partial: AssistantMessage, index: int) -> Any:
    if index < 0 or index >= len(partial.content):
        raise ValueError(f"Invalid proxy contentIndex: {index}")
    return partial.content[index]


class EventReconstructor:
    """单请求状态机；tool arguments 的增量解析状态不得跨请求复用。"""

    def __init__(self, partial: AssistantMessage) -> None:
        self.partial = partial
        self._tool_arguments: Dict[int, StreamingJsonParser] = {}

    def process(
        self, event: GatewayAssistantMessageEvent
    ) -> Optional[AssistantMessageEvent]:
        partial = self.partial
        if isinstance(event, GatewayStart):
            return StartEvent(partial=partial)
        if isinstance(event, GatewayTextStart):
            block = TextContent()
            _set_block(partial, event.content_index, block)
            return TextStartEvent(content_index=event.content_index, partial=partial)
        if isinstance(event, GatewayTextDelta):
            block = _block_at(partial, event.content_index)
            if not isinstance(block, TextContent):
                raise ValueError("Received text_delta for non-text content")
            block.text += event.delta
            return TextDeltaEvent(
                content_index=event.content_index, delta=event.delta, partial=partial
            )
        if isinstance(event, GatewayTextEnd):
            block = _block_at(partial, event.content_index)
            if not isinstance(block, TextContent):
                raise ValueError("Received text_end for non-text content")
            block.text_signature = event.content_signature
            return TextEndEvent(
                content_index=event.content_index, content=block.text, partial=partial
            )
        if isinstance(event, GatewayThinkingStart):
            block = ThinkingContent()
            _set_block(partial, event.content_index, block)
            return ThinkingStartEvent(
                content_index=event.content_index, partial=partial
            )
        if isinstance(event, GatewayThinkingDelta):
            block = _block_at(partial, event.content_index)
            if not isinstance(block, ThinkingContent):
                raise ValueError("Received thinking_delta for non-thinking content")
            block.thinking += event.delta
            return ThinkingDeltaEvent(
                content_index=event.content_index, delta=event.delta, partial=partial
            )
        if isinstance(event, GatewayThinkingEnd):
            block = _block_at(partial, event.content_index)
            if not isinstance(block, ThinkingContent):
                raise ValueError("Received thinking_end for non-thinking content")
            block.thinking_signature = event.content_signature
            return ThinkingEndEvent(
                content_index=event.content_index,
                content=block.thinking,
                partial=partial,
            )
        if isinstance(event, GatewayToolCallStart):
            block = ToolCall(id=event.id, name=event.tool_name, arguments={})
            _set_block(partial, event.content_index, block)
            self._tool_arguments[event.content_index] = StreamingJsonParser()
            return ToolCallStartEvent(
                content_index=event.content_index, partial=partial
            )
        if isinstance(event, GatewayToolCallDelta):
            block = _block_at(partial, event.content_index)
            parser = self._tool_arguments.get(event.content_index)
            if not isinstance(block, ToolCall) or parser is None:
                raise ValueError("Received toolcall_delta for non-toolCall content")
            parser.feed(event.delta)
            if isinstance(parser.value, dict):
                block.arguments = parser.value
            return ToolCallDeltaEvent(
                content_index=event.content_index, delta=event.delta, partial=partial
            )
        if isinstance(event, GatewayToolCallEnd):
            block = _block_at(partial, event.content_index)
            if not isinstance(block, ToolCall):
                raise ValueError(
                    "Received toolcall_end for non-toolCall content at index "
                    f"{event.content_index}"
                )
            for name, value in event.tool_call.model_dump(exclude_unset=True).items():
                setattr(block, name, value)
            self._tool_arguments.pop(event.content_index, None)
            return ToolCallEndEvent(
                content_index=event.content_index, tool_call=block, partial=partial
            )
        if isinstance(event, GatewayDone):
            partial.stop_reason = StopReason(event.reason)
            partial.usage = event.usage
            if event.provider_thinking_level is not None:
                partial.provider_thinking_level = event.provider_thinking_level
            return DoneEvent(reason=cast(Any, partial.stop_reason), message=partial)
        if isinstance(event, GatewayError):
            partial.stop_reason = StopReason(event.reason)
            partial.error_message = event.error_message
            partial.error_kind = event.error_kind
            partial.retry_after_ms = event.retry_after_ms
            partial.usage = event.usage
            if event.provider_thinking_level is not None:
                partial.provider_thinking_level = event.provider_thinking_level
            return ErrorEvent(reason=cast(Any, partial.stop_reason), error=partial)
        return None


__all__ = ["EventReconstructor", "new_partial"]
