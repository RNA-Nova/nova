"""
Settings utilities for merging and migration.
"""

from nova_harness.core.types.setting import Settings


def deep_merge_settings(base: Settings, overrides: Settings) -> Settings:
    """
    Deep merge settings: overrides take precedence, nested objects merge recursively.

    Args:
        base: Base settings
        overrides: Override settings

    Returns:
        Merged settings
    """
    result = base.model_copy(deep=True)

    for key in type(overrides).model_fields:
        override_value = getattr(overrides, key)

        if override_value is None:
            continue

        base_value = getattr(result, key)

        # For nested Pydantic models, merge recursively
        if hasattr(override_value, "model_fields") and hasattr(
            base_value, "model_fields"
        ):
            merged_nested = deep_merge_settings(base_value, override_value)
            setattr(result, key, merged_nested)
        elif isinstance(override_value, dict) and isinstance(base_value, dict):
            setattr(result, key, {**base_value, **override_value})
        else:
            # For primitives and lists, override value wins
            setattr(result, key, override_value)

    return result
