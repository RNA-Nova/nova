"""
Settings utilities for merging and migration.
"""

from pydantic import BaseModel

from nova_harness.core.types.config.settings import Settings


def deep_merge_settings(base: Settings, overrides: Settings) -> Settings:
    """Deep merge settings: overrides take precedence, nested objects merge recursively.

    按 ``model_fields_set`` 判定"显式提供"（对齐 TS：``undefined`` 跳过、
    显式 ``null`` 覆盖清除）：文件加载或构造时未出现的字段不影响 base；
    显式写为 null 的字段会清除 base 中的值。
    """
    result = base.model_copy(deep=True)

    for key in overrides.model_fields_set:
        override_value = getattr(overrides, key)
        base_value = getattr(result, key)

        if override_value is None:
            # 显式 null → 清除 base 值
            setattr(result, key, None)
        elif isinstance(override_value, BaseModel) and isinstance(
            base_value, BaseModel
        ):
            # 嵌套 Pydantic 模型递归合并
            merged_nested = deep_merge_settings(base_value, override_value)
            setattr(result, key, merged_nested)
        elif isinstance(override_value, dict) and isinstance(base_value, dict):
            setattr(result, key, {**base_value, **override_value})
        else:
            # 基本类型与列表：override 胜出
            setattr(result, key, override_value)

    return result
