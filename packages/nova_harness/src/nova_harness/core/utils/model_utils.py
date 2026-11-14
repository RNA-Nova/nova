"""
模型工具函数

思考级别相关能力直接复用 ``nova_ai.utils.model_utils`` 的实现
（``ModelThinkingLevel`` 含显式 ``OFF``，不再需要 None-as-off 包装）。
"""

from typing import Optional

from nova_ai.utils.model_utils import (
    clamp_thinking_level,
    get_supported_thinking_levels,
)
from nova_ai.utils.model_utils import models_are_equal as models_are_equal_upstream
from nova_ai.utils.model_utils import to_thinking_level
from nova_protocol import AnyModel


def models_are_equal(a: Optional[AnyModel], b: Optional[AnyModel]) -> bool:
    """复用 AI 正典：按 type + provider + id 判断模型相等。"""
    return models_are_equal_upstream(a, b)


__all__ = [
    "models_are_equal",
    "clamp_thinking_level",
    "get_supported_thinking_levels",
    "to_thinking_level",
]
