# teammanager/storage/memory_storage.py

"""
In-memory mounts storage for testing（完全复刻 InMemorySettingsStorage）
"""

from typing import Callable, Optional

from .base import MountsStorage
from .types import MountScope


class InMemoryMountsStorage(MountsStorage):
    """In-memory mounts storage for testing."""
    
    def __init__(self) -> None:
        self._global: Optional[str] = None
        self._project: Optional[str] = None
    
    def with_lock(
        self,
        scope: MountScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with in-memory lock."""
        current = self._global if scope == MountScope.GLOBAL else self._project
        next_content = fn(current)
        
        if next_content is not None:
            if scope == MountScope.GLOBAL:
                self._global = next_content
            else:
                self._project = next_content
    
    def seed(self, scope: MountScope, content: str) -> None:
        """测试辅助：预填充数据."""
        if scope == MountScope.GLOBAL:
            self._global = content
        else:
            self._project = content
    
    def clear(self) -> None:
        """清空."""
        self._global = None
        self._project = None