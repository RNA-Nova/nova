"""
流选项工具函数
处理模型请求的选项配置
"""

from typing import Optional

from ..types.messages import Context
from ..types.model import Model
from ..types.stream_options import SimpleStreamOptions, StreamOptions
from .estimate import CONTEXT_SAFETY_TOKENS, estimate_context_tokens

MIN_MAX_TOKENS = 1


def clamp_max_tokens_to_context(model: Model, context: Context, max_tokens: int) -> int:
    """把 max_tokens 钳制到上下文剩余窗口内（对齐 TS clampMaxTokensToContext）。

    剩余窗口 = context_window - 已用估算 - 安全余量；
    context_window 无效（<=0）时只做下限保护。
    """
    if model.context_window <= 0:
        return max(MIN_MAX_TOKENS, max_tokens)
    available = (
        model.context_window
        - estimate_context_tokens(context).tokens
        - CONTEXT_SAFETY_TOKENS
    )
    return min(max_tokens, max(MIN_MAX_TOKENS, available))


def build_base_options(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None,
    api_key: Optional[str] = None,
) -> StreamOptions:
    """
    构建基础流式选项

    Args:
        model: 模型对象
        context: 上下文（用于 max_tokens 的窗口钳制）
        options: 简单选项
        api_key: API密钥

    Returns:
        流式选项对象
    """
    requested_max_tokens = (
        options.max_tokens
        if options and options.max_tokens is not None
        else model.max_tokens
    )
    return StreamOptions(
        temperature=options.temperature if options else None,
        max_tokens=clamp_max_tokens_to_context(model, context, requested_max_tokens),
        signal=options.signal if options else None,
        api_key=api_key or (options.api_key if options else None),
        transport=options.transport if options else None,
        cache_retention=options.cache_retention if options else None,
        session_id=options.session_id if options else None,
        headers=options.headers if options else None,
        env=options.env if options else None,
        on_payload=options.on_payload if options else None,
        on_response=options.on_response if options else None,
        metadata=options.metadata if options else None,
        timeout=options.timeout if options else None,
        websocket_connect_timeout_ms=(
            options.websocket_connect_timeout_ms if options else None
        ),
        max_retries=options.max_retries if options else None,
        max_retry_delay_ms=options.max_retry_delay_ms if options else None,
    )
