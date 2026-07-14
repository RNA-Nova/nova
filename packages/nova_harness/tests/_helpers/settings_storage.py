"""内存中的 SettingsStorage 测试辅助类。"""

from typing import Callable, Optional

from nova_harness.core.config.settings.storage import SettingsStorage
from nova_harness.core.config.storage import InMemoryStorageBackend
from nova_harness.core.types.config.settings import SettingsScope


class InMemorySettingsStorage(SettingsStorage):
    """In-memory settings storage for testing."""

    def __init__(self) -> None:
        self._global = InMemoryStorageBackend()
        self._project = InMemoryStorageBackend()

    def with_lock(
        self, scope: SettingsScope, fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        backend = self._global if scope == SettingsScope.GLOBAL else self._project
        backend.with_lock(fn)
