"""
Nova 基础模型
所有 nova_ai 数据模型的基类，统一序列化行为。
"""

from typing import Any, Dict

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class NovaBaseModel(BaseModel):
    """
    Nova 数据模型基类

    统一配置：
    - **线上形态 camelCase**：``alias_generator=to_camel``——字段名 snake_case
      是 Python 内部形态（PEP8），RPC 线上 JSON 统一 camelCase（LSP/FastAPI
      惯例）；``populate_by_name=True`` 反序列化双收（snake/camel 都认）。
    - 序列化出口两形态：``model_dump()``（持久化/内部——snake_case 字段名，
      磁盘格式与存量兼容）；``dump_wire()``（RPC 线上——by_alias=True camel）。
    - Enum 序列化时使用 .value（字符串），而非 Enum 对象
    - 允许通过属性名赋值（类似 dataclass 的行为）
    - model_dump() 默认 mode='json'，与 mashumaro 的 to_dict() 行为对齐
    """

    model_config = ConfigDict(
        validate_assignment=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )

    def model_dump(self, **kwargs: Any) -> Dict[str, Any]:
        """默认使用 mode='json'，确保输出纯 Python 原生类型（如 Enum → str）"""
        kwargs.setdefault("mode", "json")
        return super().model_dump(**kwargs)

    def dump_wire(self, **kwargs: Any) -> Dict[str, Any]:
        """线上形态（RPC 边界统一出口）：camelCase 键名（by_alias=True）。"""
        kwargs.setdefault("mode", "json")
        kwargs["by_alias"] = True
        return super().model_dump(**kwargs)
