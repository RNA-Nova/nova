"""gateway：Provider 运行时单元 + Models 集合 + 模型目录存储。

对齐 TS ``src/models.ts`` 与 ``src/models-store.ts``：
- ``provider.py``：Provider / create_provider（模型目录宿主 + 协议路由 +
  ``ModelsPublication`` 发布契约）
- ``models.py``：Models / create_models（auth 网关 + provider 注册表 +
  刷新世代机制）
- ``streams.py``：``lazy_stream`` —— ProviderStreams 契约原语
  （同步签名 + 异步装配 + 错误即流终态）
- ``store.py``：ModelsStore 抽象与内存实现（动态模型目录的持久化）
"""

from .models import Models, create_models
from .provider import (
    UNSET,
    ApiImpl,
    ModelsPublication,
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
from .streams import create_setup_error_message, lazy_stream

__all__ = [
    "ApiImpl",
    "InMemoryModelsStore",
    "Models",
    "ModelsPublication",
    "ModelsStore",
    "ModelsStoreEntry",
    "Provider",
    "ProviderModelsStore",
    "ProviderStreams",
    "RefreshModelsContext",
    "UNSET",
    "create_models",
    "create_provider",
    "create_setup_error_message",
    "lazy_stream",
]
