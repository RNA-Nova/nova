"""协议选项与标准助手事件 → 公开 Gateway 事件。"""

from __future__ import annotations

import logging
from typing import Any, Optional, cast

from nova_ai import SimpleStreamOptions
from nova_ai.stream_options import ThinkingBudgets as AiThinkingBudgets
from nova_ai.utils.sanitize import redact_secrets
from nova_async import AbortSignal
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
    StreamOptions,
)
from nova_protocol import (
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
    ToolCall,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)

from .runtime import GatewayRequest, UnsupportedOptionError

logger = logging.getLogger(__name__)


def log_gateway_terminal(
    gateway_request: GatewayRequest, event: GatewayAssistantMessageEvent
) -> None:
    """两个 HTTP 方言共用同一终态日志；事件正文永不进入日志。"""
    if isinstance(event, GatewayDone):
        logger.info(
            "gateway.stream.completed",
            extra={
                **gateway_request.log_fields,
                "status": event.reason,
                "durationMs": gateway_request.elapsed_ms(),
            },
        )
    elif isinstance(event, GatewayError):
        reason = event.reason
        fields = {
            **gateway_request.log_fields,
            "status": reason,
            "durationMs": gateway_request.elapsed_ms(),
        }
        if reason == "aborted":
            logger.info("gateway.stream.cancelled", extra=fields)
        else:
            if event.error_kind is not None:
                fields["errorKind"] = event.error_kind.value
            logger.error("gateway.stream.failed", extra=fields)


def to_simple_options(
    options: StreamOptions, signal: Optional[AbortSignal]
) -> SimpleStreamOptions:
    transport = options.transport
    if transport is not None and getattr(transport, "value", transport) != "sse":
        raise UnsupportedOptionError("Only SSE transport is supported")
    budgets = options.thinking_budgets
    return SimpleStreamOptions(
        temperature=options.temperature,
        sampling_params=options.sampling_params,
        max_tokens=options.max_tokens,
        reasoning=options.reasoning,
        cache_retention=options.cache_retention,
        session_id=options.session_id,
        disable_continuation=options.disable_continuation,
        metadata=options.metadata,
        transport=options.transport,
        tool_choice=options.tool_choice,
        thinking_budgets=(
            AiThinkingBudgets(
                minimal=budgets.minimal,
                low=budgets.low,
                medium=budgets.medium,
                high=budgets.high,
            )
            if budgets is not None
            else None
        ),
        max_retries=options.max_retries,
        max_retry_delay_ms=options.max_retry_delay_ms,
        timeout=options.timeout,
        signal=signal,
    )


def to_gateway_event(
    event: AssistantMessageEvent,
) -> Optional[GatewayAssistantMessageEvent]:
    """去掉 partial，只保留线上必要的增量与终态字段。"""
    if isinstance(event, StartEvent):
        return GatewayStart()
    if isinstance(event, TextStartEvent):
        return GatewayTextStart(content_index=event.content_index)
    if isinstance(event, TextDeltaEvent):
        return GatewayTextDelta(content_index=event.content_index, delta=event.delta)
    if isinstance(event, TextEndEvent):
        return GatewayTextEnd(content_index=event.content_index)
    if isinstance(event, ThinkingStartEvent):
        return GatewayThinkingStart(content_index=event.content_index)
    if isinstance(event, ThinkingDeltaEvent):
        return GatewayThinkingDelta(
            content_index=event.content_index, delta=event.delta
        )
    if isinstance(event, ThinkingEndEvent):
        return GatewayThinkingEnd(content_index=event.content_index)
    if isinstance(event, ToolCallStartEvent):
        block = event.partial.content[event.content_index]
        if not isinstance(block, ToolCall):
            raise TypeError("toolcall_start requires a ToolCall content block")
        return GatewayToolCallStart(
            content_index=event.content_index, id=block.id, tool_name=block.name
        )
    if isinstance(event, ToolCallDeltaEvent):
        return GatewayToolCallDelta(
            content_index=event.content_index, delta=event.delta
        )
    if isinstance(event, ToolCallEndEvent):
        return GatewayToolCallEnd(
            content_index=event.content_index, tool_call=event.tool_call
        )
    if isinstance(event, DoneEvent):
        return GatewayDone(
            reason=cast(Any, event.reason),
            usage=event.message.usage,
            provider_thinking_level=event.message.provider_thinking_level,
        )
    if isinstance(event, ErrorEvent):
        message = event.error
        return GatewayError(
            reason=cast(Any, event.reason),
            usage=message.usage,
            error_message=(
                redact_secrets(message.error_message)
                if message.error_message is not None
                else None
            ),
            error_kind=message.error_kind,
            retry_after_ms=message.retry_after_ms,
            provider_thinking_level=message.provider_thinking_level,
        )
    return None


__all__ = ["to_gateway_event", "to_simple_options"]
