"""
主要API函数
提供统一的流式和非流式调用接口
"""

from typing import Optional

from ..types.enums import Api
from ..types.model import Model
from ..types.messages import Context, AssistantMessage
from .event_stream import AssistantMessageEventStream
from ..types.stream_options import SimpleStreamOptions
from ..api_impls import ProviderStreamOptions
from ..registry.api_registry import get_api_adapter


def resolve_api_adapter(api: Api):
    """
    解析API提供者
    
    Args:
        api: API类型
        
    Returns:
        API提供者实例
        
    Raises:
        ValueError: 如果没有注册对应的API提供者
    """
    provider = get_api_adapter(api)
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
    adapter = resolve_api_adapter(model.api)
    
    return adapter.stream(model, context, options)


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
    event_stream = stream(model, context, options)
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
    adapter = resolve_api_adapter(model.api)
    return adapter.stream_simple(model, context, options)


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
    event_stream = stream_simple(model, context, options)
    return await event_stream.result()


__all__ = [
    "stream",
    "complete",
    "stream_simple",
    "complete_simple",
    "resolve_api_adapter",
]
