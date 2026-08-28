"""会话级模型配置类型（scoped models 与模型轮询）。"""

from __future__ import annotations

from typing import Optional

from nova_ai import Model, ModelThinkingLevel
from nova_ai.types.base_model import NovaBaseModel


class ScopedModelConfig(NovaBaseModel):
    """带作用域的模型配置。"""

    model: Model
    thinking_level: Optional[ModelThinkingLevel] = None


class ModelCycleResult(NovaBaseModel):
    """模型轮询结果。"""

    model: Model
    thinking_level: ModelThinkingLevel
    is_scoped: bool


__all__ = ["ScopedModelConfig", "ModelCycleResult"]
