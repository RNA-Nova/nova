# teammanager/storage/base.py

"""
Mounts storage backends - 抽象接口（完全复刻 SettingsStorage）
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from .types import MountScope


class MountsStorage(ABC):
    """Abstract base class for mounts storage backends."""
    
    @abstractmethod
    def with_lock(
        self,
        scope: MountScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with storage lock."""
        pass