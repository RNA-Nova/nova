"""
代理项对处理工具
移除字符串中未配对的Unicode代理项字符
"""

import re

_UNPAIRED_SURROGATE_PATTERN = re.compile(
    r"[\uD800-\uDBFF](?![\uDC00-\uDFFF])|"  # 高代理项后无低代理项
    r"(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]"  # 低代理项前无高代理项
)


def sanitize_surrogates(text: str) -> str:
    """
    移除字符串中未配对的Unicode代理项字符。

    未配对的代理项（高代理项 0xD800-0xDBFF 没有匹配的低代理项 0xDC00-0xDFFF，
    或反之）会导致许多API提供商出现JSON序列化错误。

    基本多文种平面之外的有效的emoji和其他字符使用正确配对的代理项，
    不会受此函数影响。

    Args:
        text: 需要清理的文本

    Returns:
        移除未配对代理项后的清理文本

    Example:
        >>> # 有效的emoji（正确配对的代理项）会被保留
        >>> sanitize_surrogates("Hello 🙈 World")
        'Hello 🙈 World'

        >>> # 未配对的高代理项会被移除
        >>> unpaired = chr(0xD83D)  # 没有低代理项的高代理项
        >>> sanitize_surrogates(f"Text {unpaired} here")
        'Text  here'
    """
    if not text:
        return text
    return _UNPAIRED_SURROGATE_PATTERN.sub("", text)


__all__ = ["sanitize_surrogates"]
