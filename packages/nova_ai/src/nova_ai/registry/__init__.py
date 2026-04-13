"""
注册模块
包含所有注册相关的逻辑
"""

from .api_registry import (
    ApiProvider,
    ApiProviderRecord,
    ApiProviderRegistry,
    register_api_provider,
    get_api_provider,
    list_api_providers,
    unregister_api_provider,
    has_api_provider,
    clear_api_providers,
)

from .model_registry import (
    ModelRegistry,
    ModelProvider,
    register_model,
    get_model,
    get_models_by_provider,
    list_providers,
    list_all_models,
    find_model_by_id,
    register_models_from_dict,
)

from .builtins import (
    register_builtin_api_providers,
    register_builtin_models,
    register_all_builtins,
    reset_api_registry,
    reset_model_registry,
    reset_registry,
)

__all__ = [
    # API注册表
    "ApiProvider", "ApiProviderRecord", "ApiProviderRegistry",
    "register_api_provider", "get_api_provider", "list_api_providers",
    "unregister_api_provider", "has_api_provider", "clear_api_providers",
    
    # 模型注册表
    "ModelRegistry", "ModelProvider",
    "register_model", "get_model", "get_models_by_provider",
    "list_providers", "list_all_models", "find_model_by_id",
    "register_models_from_dict",
    
    # 内置注册
    "register_builtin_api_providers", "register_builtin_models",
    "register_all_builtins", "reset_api_registry",
    "reset_model_registry","reset_registry",
]