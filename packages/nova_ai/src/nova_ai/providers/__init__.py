"""
Provider 定义包

每个 provider 一个子目录，结构对齐 TS ``src/providers/*``：
- ``<provider>/models.py``：静态模型目录
- ``<provider>/provider.py``：provider 工厂函数
- ``<provider>/catalog.py``（可选）：``personalize`` 账户层钩子（动态目录的
  私活——kimi 订阅覆盖 / 火山 ark 筛选；治理归 ``gateway/strategies.py``）
- 构建知识归 ``nova_catalog/providers/``；models.dev 原料经
  ``catalog_materialize`` 回调物化，pi.dev 成品不重复修正
"""

from ..gateway import Provider, ProviderStreams, RefreshModelsContext, create_provider
from .all import (
    builtin_models,
    builtin_providers,
    get_builtin_classifier_model,
    get_builtin_classifier_models,
    get_builtin_image_model,
    get_builtin_image_models,
    get_builtin_model,
    get_builtin_models,
)
from .anthropic import ANTHROPIC_MODELS, anthropic_provider
from .kimi_coding import KIMI_CODING_MODELS, kimi_coding_provider
from .moonshotai import MOONSHOTAI_MODELS, moonshotai_provider
from .moonshotai_cn import MOONSHOTAI_CN_MODELS, moonshotai_cn_provider
from .openrouter import (
    OPENROUTER_CLASSIFIER_MODELS,
    OPENROUTER_IMAGE_MODELS,
    openrouter_provider,
)
from .volcengine import VOLCENGINE_MODELS, volcengine_provider

__all__ = [
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_provider",
    "builtin_providers",
    "builtin_models",
    "get_builtin_classifier_model",
    "get_builtin_classifier_models",
    "get_builtin_image_model",
    "get_builtin_image_models",
    "get_builtin_model",
    "get_builtin_models",
    "ANTHROPIC_MODELS",
    "anthropic_provider",
    "KIMI_CODING_MODELS",
    "kimi_coding_provider",
    "MOONSHOTAI_MODELS",
    "moonshotai_provider",
    "MOONSHOTAI_CN_MODELS",
    "moonshotai_cn_provider",
    "OPENROUTER_CLASSIFIER_MODELS",
    "OPENROUTER_IMAGE_MODELS",
    "openrouter_provider",
    "VOLCENGINE_MODELS",
    "volcengine_provider",
]
