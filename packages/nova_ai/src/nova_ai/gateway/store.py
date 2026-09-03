"""Models 持久化存储。

对齐 TS ``src/models-store.ts``：按 provider id 持久化动态模型目录。
"""

from typing import Dict, List, Optional, Protocol

from ..types.base_model import NovaBaseModel
from ..types.model import Model


class ModelsStoreEntry(NovaBaseModel):
    """单个 provider 的模型目录条目（文件型 store 的 JSON schema）。"""

    models: List[Model]
    checked_at: Optional[int] = None
    # 远程目录 Last-Modified 头的 Unix 毫秒时间戳（对齐 TS lastModified）。
    # 与基线 generatedAt 做新鲜度竞速：早于基线的 overlay 被忽略。
    last_modified: Optional[int] = None
    # 远程目录 ETag 原样存储（含引号），条件请求时回传 If-None-Match
    etag: Optional[str] = None


class ModelsStore(Protocol):
    """按 provider id 持久化模型目录的抽象。"""

    async def read(self, provider_id: str) -> Optional[ModelsStoreEntry]:
        """读取 provider 的模型目录。"""
        ...

    async def write(self, provider_id: str, entry: ModelsStoreEntry) -> None:
        """写入 provider 的模型目录。"""
        ...

    async def delete(self, provider_id: str) -> None:
        """删除 provider 的模型目录。"""
        ...


class ProviderModelsStore(Protocol):
    """限定到单个 provider 的模型目录存储。"""

    async def read(self) -> Optional[ModelsStoreEntry]:
        """读取当前 provider 的模型目录。"""
        ...

    async def write(self, entry: ModelsStoreEntry) -> None:
        """写入当前 provider 的模型目录。"""
        ...

    async def delete(self) -> None:
        """删除当前 provider 的模型目录。"""
        ...


class InMemoryModelsStore:
    """内存 ModelsStore 实现。"""

    def __init__(self) -> None:
        self._entries: Dict[str, ModelsStoreEntry] = {}

    async def read(self, provider_id: str) -> Optional[ModelsStoreEntry]:
        entry = self._entries.get(provider_id)
        if entry is None:
            return None
        return ModelsStoreEntry(models=list(entry.models), checked_at=entry.checked_at)

    async def write(self, provider_id: str, entry: ModelsStoreEntry) -> None:
        self._entries[provider_id] = ModelsStoreEntry(
            models=list(entry.models), checked_at=entry.checked_at
        )

    async def delete(self, provider_id: str) -> None:
        self._entries.pop(provider_id, None)


__all__ = [
    "InMemoryModelsStore",
    "ModelsStore",
    "ModelsStoreEntry",
    "ProviderModelsStore",
]
