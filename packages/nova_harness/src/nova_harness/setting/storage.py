"""
Settings storage backends.
"""

import json
import os
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional

from filelock import FileLock, Timeout

from ..config import CONFIG_DIR_NAME, get_agent_dir
from .types import Settings, SettingsScope


class SettingsStorage(ABC):
    """Abstract base class for settings storage backends."""
    
    @abstractmethod
    def with_lock(
        self,
        scope: SettingsScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with storage lock."""
        pass


class FileSettingsStorage(SettingsStorage):
    """File-based settings storage with locking."""
    
    def __init__(
        self,
        cwd: str = os.getcwd(),
        agent_dir: str = get_agent_dir(),
        timeout: float = 30.0
    ) -> None:
        self._global_settings_path = Path(agent_dir) / "settings.json"
        self._project_settings_path = Path(cwd) / CONFIG_DIR_NAME / "settings.json"
        self._lock = threading.Lock()
        self.timeout = timeout
        
    def with_lock(
        self,
        scope: SettingsScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with file lock."""
        path = (
            self._global_settings_path 
            if scope == SettingsScope.GLOBAL 
            else self._project_settings_path
        )
        
        lock_path = str(path) + ".lock"
        lock = FileLock(lock_path, timeout=self.timeout)
        
        try:
            with lock:
                current = None
                if path.exists():
                    current = path.read_text(encoding="utf-8")
                
                next_content = fn(current)
                
                if next_content is not None:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(next_content, encoding="utf-8")
        except Timeout:
            raise RuntimeError(f"Could not acquire lock for {path}")


class InMemorySettingsStorage(SettingsStorage):
    """In-memory settings storage for testing."""
    
    def __init__(self) -> None:
        self._global: Optional[str] = None
        self._project: Optional[str] = None
    
    def with_lock(
        self,
        scope: SettingsScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with in-memory lock."""
        current = self._global if scope == SettingsScope.GLOBAL else self._project
        next_content = fn(current)
        
        if next_content is not None:
            if scope == SettingsScope.GLOBAL:
                self._global = next_content
            else:
                self._project = next_content