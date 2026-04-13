"""
Settings utilities for merging and migration.
"""

from copy import deepcopy
from typing import Any

from .types import Settings


def deep_merge_settings(base: Settings, overrides: Settings) -> Settings:
    """
    Deep merge settings: overrides take precedence, nested objects merge recursively.
    
    Args:
        base: Base settings
        overrides: Override settings
        
    Returns:
        Merged settings
    """
    result = deepcopy(base)
    
    for key in overrides.__dataclass_fields__:
        override_value = getattr(overrides, key)
        
        if override_value is None:
            continue
            
        base_value = getattr(result, key)
        
        # For nested dataclasses, merge recursively
        if (
            hasattr(override_value, '__dataclass_fields__') and
            hasattr(base_value, '__dataclass_fields__')
        ):
            merged_nested = deep_merge_settings(base_value, override_value)
            setattr(result, key, merged_nested)
        elif isinstance(override_value, dict) and isinstance(base_value, dict):
            setattr(result, key, {**base_value, **override_value})
        else:
            # For primitives and lists, override value wins
            setattr(result, key, override_value)
    
    return result