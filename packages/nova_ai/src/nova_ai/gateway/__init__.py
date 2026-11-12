"""gateway：Provider 运行时单元 + ModelsManager 集合 + 模型目录存储。

对齐 TS ``src/models.ts`` 与 ``src/models-store.ts``：
- ``provider.py``：Provider（模型目录宿主 + 协议路由 + ModelsPublication 契约）
- ``factory.py``：create_provider（组合 Provider 与刷新策略，解除模块环）
- ``models.py``：ModelsManager / create_models_manager（auth 网关 + provider
  注册表 + 刷新世代机制 + 目录站容器路由）
- ``catalog.py``：目录契约词汇（CatalogOutcome / CatalogService 站契约 /
  CatalogServices 容器形状 / FetchCatalogFn）
- ``streams.py``：``lazy_stream`` —— ProviderStreams 契约原语
  （同步签名 + 异步装配 + 错误即流终态）
- ``diagnostics.py``：``DiagnosticsCollector``——诊断批模式标准装置
  （无条件收集 + 可选实时缝；词汇住 nova_protocol，装置随本网关层）
- ``store.py``：ModelsStore 抽象与内存实现（动态模型目录的持久化）
"""

from .catalog import CatalogOutcome, CatalogService, CatalogServices, FetchCatalogFn
from .diagnostics import DiagnosticsCollector
from .factory import create_provider
from .models import ModelsManager, create_models_manager
from .provider import (
    UNSET,
    ApiImpl,
    ClassifiersImpl,
    ImagesImpl,
    ModelsPublication,
    Provider,
    ProviderClassifier,
    ProviderImages,
    ProviderStreams,
    RefreshModelsContext,
    merge_models,
)
from .store import InMemoryModelsStore, ModelsStore
from .streams import create_setup_error_message, lazy_stream

__all__ = [
    "ApiImpl",
    "CatalogOutcome",
    "CatalogService",
    "CatalogServices",
    "ClassifiersImpl",
    "DiagnosticsCollector",
    "FetchCatalogFn",
    "ImagesImpl",
    "InMemoryModelsStore",
    "ModelsManager",
    "ModelsPublication",
    "ModelsStore",
    "Provider",
    "ProviderClassifier",
    "ProviderImages",
    "ProviderStreams",
    "RefreshModelsContext",
    "UNSET",
    "create_models_manager",
    "create_provider",
    "create_setup_error_message",
    "lazy_stream",
    "merge_models",
]
