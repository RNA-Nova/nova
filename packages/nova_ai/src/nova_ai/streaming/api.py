"""
主要API函数
提供统一的流式和非流式调用接口
"""

import asyncio
from typing import Optional, TypeVar

from ..core.enums import Api
from ..models import Model
from ..core.messages import Context, AssistantMessage
from .event_stream import AssistantMessageEventStream
from ..utils.stream_options import StreamOptions, SimpleStreamOptions
from ..providers import ProviderStreamOptions
from ..utils.env import get_env_api_key
from ..registry.api_registry import get_api_provider


TApi = TypeVar('TApi', bound=Api)


def resolve_api_provider(api: Api):
    """
    解析API提供者
    
    Args:
        api: API类型
        
    Returns:
        API提供者实例
        
    Raises:
        ValueError: 如果没有注册对应的API提供者
    """
    provider = get_api_provider(api)
    if provider is None:
        raise ValueError(f"No API provider registered for api: {api}")
    return provider


def stream(
    model: Model,
    context: Context,
    options: Optional[ProviderStreamOptions] = None
) -> AssistantMessageEventStream:
    """
    流式调用模型
    
    Args:
        model: 模型对象
        context: 上下文
        options: 流式选项
        
    Returns:
        助手消息事件流
    """
    provider = resolve_api_provider(model.api)
    
    stream_options = None
    if options:
        stream_options = ProviderStreamOptions(
            temperature=options.temperature,
            max_tokens=options.max_tokens,
            signal=options.signal,
            api_key=options.api_key,
            transport=options.transport,
            cache_retention=options.cache_retention,
            session_id=options.session_id,
            headers=options.headers,
            on_payload=options.on_payload,
            max_retry_delay_ms=options.max_retry_delay_ms,
            metadata=options.metadata,
        )
    
    return provider.stream(model, context, stream_options)


async def complete(
    model: Model,
    context: Context,
    options: Optional[ProviderStreamOptions] = None
) -> AssistantMessage:
    """
    完成调用模型（非流式）
    
    Args:
        model: 模型对象
        context: 上下文
        options: 流式选项
        
    Returns:
        完整的助手消息
    """
    event_stream = await stream(model, context, options)
    return await event_stream.result()


def stream_simple(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None
) -> AssistantMessageEventStream:
    """
    简化的流式调用
    
    Args:
        model: 模型对象
        context: 上下文
        options: 简化选项
        
    Returns:
        助手消息事件流
    """
    provider = resolve_api_provider(model.api)
    return provider.stream_simple(model, context, options)


async def complete_simple(
    model: Model,
    context: Context,
    options: Optional[SimpleStreamOptions] = None
) -> AssistantMessage:
    """
    简化的完成调用
    
    Args:
        model: 模型对象
        context: 上下文
        options: 简化选项
        
    Returns:
        完整的助手消息
    """
    event_stream = await stream_simple(model, context, options)
    return await event_stream.result()


__all__ = [
    "stream",
    "complete",
    "stream_simple",
    "complete_simple",
    "resolve_api_provider",
]
