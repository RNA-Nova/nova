"""AI 调用选项与公开协议选项白名单之间的转换。"""

from __future__ import annotations

from typing import Optional

from nova_gateway_protocol import StreamOptions as WireStreamOptions
from nova_gateway_protocol import ThinkingBudgets

from nova_ai import SimpleStreamOptions, StreamOptions


def as_simple_options(options: Optional[StreamOptions]) -> SimpleStreamOptions:
    """完整 ProviderStreams 选项保留本地字段，推理由 Simple 入口补充。"""
    if options is None:
        return SimpleStreamOptions()
    if isinstance(options, SimpleStreamOptions):
        return options
    return SimpleStreamOptions(
        temperature=options.temperature,
        max_tokens=options.max_tokens,
        api_key=options.api_key,
        transport=options.transport,
        cache_retention=options.cache_retention,
        session_id=options.session_id,
        disable_continuation=options.disable_continuation,
        headers=options.headers,
        metadata=options.metadata,
        identity=options.identity,
        env=options.env,
        timeout=options.timeout,
        max_retries=options.max_retries,
        max_retry_delay_ms=options.max_retry_delay_ms,
        sampling_params=options.sampling_params,
        tool_choice=options.tool_choice,
        signal=options.signal,
        on_payload=options.on_payload,
        on_response=options.on_response,
        on_provider_stream_event=options.on_provider_stream_event,
        transform_headers=options.transform_headers,
    )


def to_wire_options(options: SimpleStreamOptions) -> WireStreamOptions:
    budgets = options.thinking_budgets
    return WireStreamOptions(
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
            ThinkingBudgets(
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
    )


__all__ = ["as_simple_options", "to_wire_options"]
