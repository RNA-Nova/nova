"""上下文溢出错误检测。

对齐 TS：各 provider 在输入超出模型上下文窗口时返回的错误文案模式库
（**词表正典归 ``errors.py``——归一枢纽的单一正典源，本模块 import
复用**），以及非溢出错误（限流/服务器错误）的排除模式。
"""

from typing import Optional

from nova_protocol import AssistantMessage, NovaErrorKind, StopReason

from .errors import NON_OVERFLOW_PATTERNS, OVERFLOW_PATTERNS


def is_context_overflow(
    message: AssistantMessage, context_window: Optional[int] = None
) -> bool:
    """判断 assistant 消息是否为上下文溢出错误。

    **kind 优先**（``error_kind`` 非 None 时信任归一单点的分类，不再做文本
    判定——铁律二）；``error_kind`` 缺席（旧数据）时回退文本模式匹配。
    """
    # Case 1: Error message patterns
    if message.stop_reason == StopReason.ERROR:
        if message.error_kind is NovaErrorKind.CONTEXT_OVERFLOW:
            return True
        if (
            message.error_kind is None
            and message.error_message
            and not any(
                pattern.search(message.error_message)
                for pattern in NON_OVERFLOW_PATTERNS
            )
            and any(
                pattern.search(message.error_message) for pattern in OVERFLOW_PATTERNS
            )
        ):
            return True

    # Case 2: Silent overflow (z.ai style) - successful but usage exceeds context
    if context_window and message.stop_reason == StopReason.STOP:
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens > context_window:
            return True

    # Case 3: Length-stop overflow (Xiaomi MiMo style) - server truncates input
    # to fit context window, leaving no room for output.
    if (
        context_window
        and message.stop_reason == StopReason.LENGTH
        and message.usage.output == 0
    ):
        input_tokens = message.usage.input + message.usage.cache_read
        if input_tokens >= context_window * 0.99:
            return True

    return False
