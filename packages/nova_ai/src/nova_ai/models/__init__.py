"""
模型模块
包含所有提供商模型的定义和注册表
"""

from .base import Model, ModelCost, calculate_cost, supports_xhigh_thinking, models_are_equal
from .openai import OPENAI_MODELS, get_openai_model, list_openai_models
from .anthropic import ANTHROPIC_MODELS, get_anthropic_model, list_anthropic_models
from .google import GOOGLE_MODELS, get_google_model, list_google_models
from .volcengine import VOLCENGINE_MODELS, get_volcengine_model, list_volcengine_models

__all__ = [
    # 基础
    "Model",
    "ModelCost",
    "calculate_cost",
    "supports_xhigh_thinking",
    "models_are_equal",
    
    # 按提供商分组的模型
    "MODELS_BY_PROVIDER",
    
    # OpenAI
    "OPENAI_MODELS",
    "get_openai_model",
    "list_openai_models",
    
    # Anthropic
    "ANTHROPIC_MODELS",
    "get_anthropic_model",
    "list_anthropic_models",
    
    # Google
    "GOOGLE_MODELS",
    "get_google_model",
    "list_google_models",

    # Volcengine
    "VOLCENGINE_MODELS",
    "get_volcengine_model",
    "list_volcengine_models"

    
]