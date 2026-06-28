"""
上下文工具函数
"""

from typing import Optional

from nova_ai import AssistantMessage


def is_context_overflow(
    message: AssistantMessage, context_window: Optional[int] = None
) -> bool:
    """
    检查消息是否导致上下文溢出

    Args:
        message: 助手消息（包含 usage 统计）
        context_window: 上下文窗口大小，默认为 None

    Returns:
        是否溢出
    """
    if context_window is None or message.usage is None:
        return False

    input_tokens = message.usage.input + message.usage.cache_read
    if input_tokens > context_window:
        return True

    return False
