"""
模型工具函数

思考级别相关能力直接复用 ``nova_ai.utils.model_utils`` 的实现
（``ModelThinkingLevel`` 含显式 ``OFF``，不再需要 None-as-off 包装）。
"""

from typing import Optional

from nova_ai import Model
from nova_ai.utils.model_utils import (
    clamp_thinking_level,
    get_supported_thinking_levels,
    to_thinking_level,
)


def models_are_equal(
    a: Optional[Model],
    b: Optional[Model],
) -> bool:
    """
    检查两个模型是否相等（比较 id 和 provider）

    如果任一模型为 None，返回 False

    Args:
        a: 第一个模型
        b: 第二个模型

    Returns:
        两个模型是否相等
    """
    if a is None or b is None:
        return False
    return a.id == b.id and a.provider == b.provider


__all__ = [
    "models_are_equal",
    "clamp_thinking_level",
    "get_supported_thinking_levels",
    "to_thinking_level",
]
