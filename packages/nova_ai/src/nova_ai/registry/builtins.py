"""
内置组件注册
集中注册所有内置的 API 适配器和模型
"""

from .api_registry import clear_api_adapters, register_api_adapter
from .model_registry import clear_model_registry, register_models_from_dict
from ..types.enums import KnownProvider

# 导入API提供者

try:
    from ..api_impls.openai_completions import OpenAICompletionsAdapter
    HAS_OPENAI_COMPLETIONS = True
except ImportError:
    HAS_OPENAI_COMPLETIONS = False


# 导入模型数据
try:
    from ..models.volcengine import VOLCENGINE_MODELS
    HAS_VOLCENGINE_MODELS = True
except ImportError:
    HAS_VOLCENGINE_MODELS = False


def register_builtin_api_adapters() -> None:
    """注册所有内置的 API 适配器"""
    
    # OpenAI Completions
    if HAS_OPENAI_COMPLETIONS:
        register_api_adapter(OpenAICompletionsAdapter())


def register_builtin_models() -> None:
    """注册所有内置的模型"""
    
    # Volcengine 模型
    if HAS_VOLCENGINE_MODELS:
        register_models_from_dict(KnownProvider.VOLCENGINE, VOLCENGINE_MODELS)


def register_all_builtins() -> None:
    """注册所有内置组件（API提供者和模型）"""
    register_builtin_api_adapters()
    register_builtin_models()


def reset_api_adapter_registry() -> None:
    """重置API提供者注册表并重新注册内置API提供者"""
    clear_api_adapters()
    register_builtin_api_adapters()


def reset_model_registry() -> None:
    """重置模型注册表并重新注册内置模型"""
    clear_model_registry()
    register_builtin_models()


def reset_registry() -> None:
    """重置所有注册表（API提供者和模型）"""
    reset_api_adapter_registry()
    reset_model_registry()


__all__ = [
    "register_builtin_api_adapters",
    "register_builtin_models",
    "register_all_builtins",
    "reset_api_adapter_registry",
    "reset_model_registry",
    "reset_registry",
]