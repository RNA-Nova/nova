"""Agent 模型相关纯数据类型。"""

from __future__ import annotations

from typing import Optional

from nova_ai import Model, ThinkingLevel
from nova_ai.types.base_model import NovaBaseModel


class ScopedModelConfig(NovaBaseModel):
    """带作用域的模型配置。"""

    model: Model
    thinking_level: Optional[ThinkingLevel] = None


class ModelCycleResult(NovaBaseModel):
    """模型轮询结果。"""

    model: Model
    thinking_level: ThinkingLevel
    is_scoped: bool


__all__ = ["ScopedModelConfig", "ModelCycleResult"]
