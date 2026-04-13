"""
内置组件注册
集中注册所有内置的API提供者和模型
"""

from .api_registry import clear_api_providers, register_api_provider
from .model_registry import clear_model_registry, register_models_from_dict
from ..core.enums import KnownApi, KnownProvider

# 导入API提供者

try:
    from ..providers.openai_completions import stream_openai_completions, stream_simple_openai_completions
    HAS_OPENAI_COMPLETIONS = True
except ImportError:
    HAS_OPENAI_COMPLETIONS = False


# 导入模型数据
try:
    from ..models.openai import OPENAI_MODELS
    HAS_OPENAI_MODELS = True
except ImportError:
    HAS_OPENAI_MODELS = False

try:
    from ..models.anthropic import ANTHROPIC_MODELS
    HAS_ANTHROPIC_MODELS = True
except ImportError:
    HAS_ANTHROPIC_MODELS = False

try:
    from ..models.google import GOOGLE_MODELS
    HAS_GOOGLE_MODELS = True
except ImportError:
    HAS_GOOGLE_MODELS = False

try:
    from ..models.volcengine import VOLCENGINE_MODELS
    HAS_VOLCENGINE_MODELS = True
except ImportError:
    HAS_VOLCENGINE_MODELS = False


def register_builtin_api_providers() -> None:
    """注册所有内置的API提供者"""
    
    # OpenAI Completions
    if HAS_OPENAI_COMPLETIONS:
        register_api_provider({
            "api": KnownApi.OPENAI_COMPLETIONS,
            "stream": stream_openai_completions,
            "stream_simple": stream_simple_openai_completions,
        })


def register_builtin_models() -> None:
    """注册所有内置的模型"""
    
    # OpenAI 模型
    if HAS_OPENAI_MODELS:
        register_models_from_dict(KnownProvider.OPENAI, OPENAI_MODELS)
    
    # Anthropic 模型
    if HAS_ANTHROPIC_MODELS:
        register_models_from_dict(KnownProvider.ANTHROPIC, ANTHROPIC_MODELS)
    
    # Google 模型
    if HAS_GOOGLE_MODELS:
        register_models_from_dict(KnownProvider.GOOGLE, GOOGLE_MODELS)

    # Volcengine 模型
    if HAS_VOLCENGINE_MODELS:
        register_models_from_dict(KnownProvider.VOLCENGINE, VOLCENGINE_MODELS)


def register_all_builtins() -> None:
    """注册所有内置组件（API提供者和模型）"""
    register_builtin_api_providers()
    register_builtin_models()


def reset_api_registry() -> None:
    """重置API提供者注册表并重新注册内置API提供者"""
    clear_api_providers()
    register_builtin_api_providers()


def reset_model_registry() -> None:
    """重置模型注册表并重新注册内置模型"""
    clear_model_registry()
    register_builtin_models()


def reset_registry() -> None:
    """重置所有注册表（API提供者和模型）"""
    reset_api_registry()
    reset_model_registry()


__all__ = [
    "register_builtin_api_providers",
    "register_builtin_models",
    "register_all_builtins",
    "reset_api_registry",
    "reset_model_registry",
    "reset_registry",
]