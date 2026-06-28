"""
Nova 基础模型
所有 nova_ai 数据模型的基类，统一序列化行为。
"""

from typing import Any, Dict
from pydantic import BaseModel, ConfigDict


class NovaBaseModel(BaseModel):
    """
    Nova 数据模型基类

    统一配置：
    - Enum 序列化时使用 .value（字符串），而非 Enum 对象
    - 允许通过属性名赋值（类似 dataclass 的行为）
    - model_dump() 默认 mode='json'，与 mashumaro 的 to_dict() 行为对齐
    """

    model_config = ConfigDict(
        validate_assignment=True,
    )

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """默认使用 mode='json'，确保输出纯 Python 原生类型（如 Enum → str）"""
        kwargs.setdefault("mode", "json")
        return super().model_dump(**kwargs)
