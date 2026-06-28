"""
模型工具函数
"""

from typing import List, Optional

from nova_ai import Model, ThinkingLevel
from nova_ai.utils.model_utils import (
    get_supported_thinking_levels as _nova_get_supported_thinking_levels,
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


def get_supported_thinking_levels(model: Model) -> List[ThinkingLevel]:
    """
    获取模型支持的思考级别列表，返回 ThinkingLevel 枚举值。

    规则与 ``nova_ai.utils.model_utils.get_supported_thinking_levels`` 保持一致：
    - 模型不支持 reasoning 时，只返回 ``[ThinkingLevel.OFF]``（用 None 表示）
    - ``thinking_level_map`` 中显式标记为 null 的级别不受支持
    - ``xhigh`` 默认不受支持，除非显式定义
    """
    if not model.reasoning:
        return [None]

    levels: List[ThinkingLevel] = []
    for level_str in _nova_get_supported_thinking_levels(model):
        if level_str == "off":
            levels.append(None)
            continue
        try:
            levels.append(ThinkingLevel(level_str))
        except ValueError:
            continue
    return levels


def clamp_thinking_level(
    model: Model, level: Optional[ThinkingLevel]
) -> Optional[ThinkingLevel]:
    """
    将思考级别钳制到模型支持的范围内。

    如果指定级别不受支持，则返回最接近的可用级别：
    - 优先返回 ``medium``
    - 其次返回第一个可用级别
    - 模型不支持 reasoning 时返回 ``None``（off）
    """
    supported = get_supported_thinking_levels(model)
    if not supported:
        return None
    if level in supported:
        return level

    preferred_order = [
        ThinkingLevel.MEDIUM,
        ThinkingLevel.LOW,
        ThinkingLevel.HIGH,
        ThinkingLevel.MINIMAL,
        ThinkingLevel.XHIGH,
    ]
    for preferred in preferred_order:
        if preferred in supported:
            return preferred
    return supported[0]
