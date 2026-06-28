"""
注册模块
包含所有注册相关的逻辑
"""

from ..types import ApiAdapter
from .api_registry import (
    ApiRegistry,
    register_api_adapter,
    get_api_adapter,
    list_api_adapters,
    unregister_api_adapter,
    has_api_adapter,
    clear_api_adapters,
)

from .model_registry import (
    ModelRegistry,
    register_model,
    get_model,
    get_models_by_provider,
    list_providers,
    list_all_models,
    find_model_by_id,
    register_models_from_dict,
)

from .builtins import (
    register_builtin_api_adapters,
    register_builtin_models,
    register_all_builtins,
    reset_api_adapter_registry,
    reset_model_registry,
    reset_registry,
)

__all__ = [
    # API注册表
    "ApiAdapter", "ApiRegistry",
    "register_api_adapter", "get_api_adapter", "list_api_adapters",
    "unregister_api_adapter", "has_api_adapter", "clear_api_adapters",
    
    # 模型注册表
    "ModelRegistry",
    "register_model", "get_model", "get_models_by_provider",
    "list_providers", "list_all_models", "find_model_by_id",
    "register_models_from_dict",
    
    # 内置注册
    "register_builtin_api_adapters", "register_builtin_models",
    "register_all_builtins", "reset_api_adapter_registry",
    "reset_model_registry","reset_registry",
]