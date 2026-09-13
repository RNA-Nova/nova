"""Models 持久化存储。

对齐 TS ``src/models-store.ts``：按 provider id 持久化动态模型目录。
落盘 schema ``ModelsStoreEntry`` 住 ``nova_protocol``（跨组件序列化词汇）；
本模块只有存储契约（Protocol）与内存实现。
"""

from typing import Dict, Optional, Protocol

from nova_protocol import ModelsStoreEntry


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
        return entry.model_copy(deep=True)

    async def write(self, provider_id: str, entry: ModelsStoreEntry) -> None:
        self._entries[provider_id] = entry.model_copy(deep=True)

    async def delete(self, provider_id: str) -> None:
        self._entries.pop(provider_id, None)


__all__ = [
    "InMemoryModelsStore",
    "ModelsStore",
    "ProviderModelsStore",
]
