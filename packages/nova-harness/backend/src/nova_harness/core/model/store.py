# model_runtime/store.py
"""动态模型目录的持久化存储（对齐 TS ``core/models-store.ts``）。

nova_ai 已提供 ``InMemoryModelsStore``；这里补文件型实现，
复用 harness 的 ``FileStorageBackend``（文件锁 + 原子写）。
"""

from __future__ import annotations

import json
import os
from typing import Dict, Optional

from nova_ai.gateway.store import ModelsStoreEntry

from nova_harness.core.config.defaults import MODELS_STORE_FILE_NAME, get_agent_dir
from nova_harness.core.config.storage import FileStorageBackend


class FileModelsStore:
    """JSON 文件型 ModelsStore：按 provider id 持久化动态模型目录。"""

    def __init__(self, path: Optional[str] = None) -> None:
        if path is None:
            path = os.path.join(get_agent_dir(), MODELS_STORE_FILE_NAME)
        self._storage = FileStorageBackend(
            path,
            file_mode=0o600,
            dir_mode=0o700,
            initial_content="{}",
        )

    @staticmethod
    def _parse(content: Optional[str]) -> Dict[str, dict]:
        if not content:
            return {}
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return raw if isinstance(raw, dict) else {}

    async def read(self, provider_id: str) -> Optional[ModelsStoreEntry]:
        result: Dict[str, Optional[ModelsStoreEntry]] = {"entry": None}

        def _read(content: Optional[str]) -> None:
            raw = self._parse(content).get(provider_id)
            if raw is not None:
                result["entry"] = ModelsStoreEntry.model_validate(raw)
            return None

        self._storage.with_lock(_read)
        return result["entry"]

    async def write(self, provider_id: str, entry: ModelsStoreEntry) -> None:
        def _write(content: Optional[str]) -> str:
            current = self._parse(content)
            current[provider_id] = entry.model_dump()
            return json.dumps(current, indent=2)

        self._storage.with_lock(_write)

    async def delete(self, provider_id: str) -> None:
        def _delete(content: Optional[str]) -> str:
            current = self._parse(content)
            current.pop(provider_id, None)
            return json.dumps(current, indent=2)

        self._storage.with_lock(_delete)


__all__ = ["FileModelsStore"]
