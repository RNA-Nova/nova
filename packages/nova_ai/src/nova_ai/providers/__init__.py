"""
Provider 定义包

每个 provider 一个子目录，结构对齐 TS ``src/providers/*``：
- ``<provider>/models.py``：静态模型目录
- ``<provider>/provider.py``：provider 工厂函数
- ``<provider>/catalog.py``（可选）：``fetch_models`` 回调（动态目录内容源）
- ``<provider>/corrections.py``（可选）：修正知识模块（知识随行）
"""

from ..gateway import Provider, ProviderStreams, RefreshModelsContext, create_provider
from .all import (
    builtin_models,
    builtin_providers,
    get_builtin_model,
    get_builtin_models,
)
from .anthropic import (
    ANTHROPIC_MODELS,
    anthropic_provider,
    get_anthropic_model,
    list_anthropic_models,
)
from .kimi_coding import (
    KIMI_CODING_MODELS,
    get_kimi_coding_model,
    kimi_coding_provider,
    list_kimi_coding_models,
)
from .moonshotai import (
    MOONSHOTAI_MODELS,
    get_moonshotai_model,
    list_moonshotai_models,
    moonshotai_provider,
)
from .moonshotai_cn import (
    MOONSHOTAI_CN_MODELS,
    get_moonshotai_cn_model,
    list_moonshotai_cn_models,
    moonshotai_cn_provider,
)
from .volcengine import (
    VOLCENGINE_MODELS,
    get_volcengine_model,
    list_volcengine_models,
    volcengine_provider,
)

__all__ = [
    "Provider",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_provider",
    "builtin_providers",
    "builtin_models",
    "get_builtin_model",
    "get_builtin_models",
    "ANTHROPIC_MODELS",
    "anthropic_provider",
    "get_anthropic_model",
    "list_anthropic_models",
    "KIMI_CODING_MODELS",
    "get_kimi_coding_model",
    "list_kimi_coding_models",
    "kimi_coding_provider",
    "MOONSHOTAI_MODELS",
    "get_moonshotai_model",
    "list_moonshotai_models",
    "moonshotai_provider",
    "MOONSHOTAI_CN_MODELS",
    "get_moonshotai_cn_model",
    "list_moonshotai_cn_models",
    "moonshotai_cn_provider",
    "VOLCENGINE_MODELS",
    "get_volcengine_model",
    "list_volcengine_models",
    "volcengine_provider",
]
