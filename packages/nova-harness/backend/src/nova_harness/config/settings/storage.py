"""
Settings storage backends.
"""

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

from nova_harness.core.config.defaults import (
    CONFIG_DIR_NAME,
    SETTINGS_FILE_NAME,
    get_agent_dir,
)
from nova_harness.core.config.storage import FileStorageBackend
from nova_harness.core.types.config.settings import SettingsScope


class SettingsStorage(ABC):
    """Abstract base class for settings storage backends."""

    @abstractmethod
    def with_lock(
        self, scope: SettingsScope, fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with storage lock."""
        raise NotImplementedError


class FileSettingsStorage(SettingsStorage):
    """File-based settings storage with locking."""

    def __init__(
        self,
        cwd: str = os.getcwd(),
        agent_dir: str = str(get_agent_dir()),
        timeout: float = 30.0,
    ) -> None:
        self._global = FileStorageBackend(
            Path(agent_dir) / SETTINGS_FILE_NAME,
            timeout=timeout,
        )
        self._project = FileStorageBackend(
            Path(cwd) / CONFIG_DIR_NAME / SETTINGS_FILE_NAME,
            timeout=timeout,
        )

    def with_lock(
        self, scope: SettingsScope, fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        backend = self._global if scope == SettingsScope.GLOBAL else self._project
        backend.with_lock(fn)
