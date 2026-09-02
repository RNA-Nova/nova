"""gateway：Provider 运行时单元 + Models 集合 + 模型目录存储。

对齐 TS ``src/models.ts`` 与 ``src/models-store.ts``：
- ``provider.py``：Provider / create_provider（模型目录宿主 + 协议路由）
- ``models.py``：Models / create_models（auth 网关 + provider 注册表）
- ``store.py``：ModelsStore 抽象与内存实现（动态模型目录的持久化）
"""

from .models import Models, _ProviderModelsStoreAdapter, create_models
from .provider import (
    ApiImpl,
    Provider,
    ProviderStreams,
    RefreshModelsContext,
    create_provider,
)
from .store import (
    InMemoryModelsStore,
    ModelsStore,
    ModelsStoreEntry,
    ProviderModelsStore,
)

__all__ = [
    "ApiImpl",
    "InMemoryModelsStore",
    "Models",
    "ModelsStore",
    "ModelsStoreEntry",
    "Provider",
    "ProviderModelsStore",
    "ProviderStreams",
    "RefreshModelsContext",
    "create_models",
    "create_provider",
]
