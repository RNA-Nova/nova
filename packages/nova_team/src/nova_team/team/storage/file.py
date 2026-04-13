# teammanager/storage/file_storage.py

"""
File-based mounts storage with locking
"""

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from filelock import FileLock, Timeout

from ...config import CONFIG_DIR_NAME, get_agent_dir
from .base import MountsStorage
from .types import MountScope


class FileMountsStorage(MountsStorage):
    """File-based mounts storage with locking."""
    
    def __init__(
        self,
        cwd: str = os.getcwd(),
        agent_dir: str = None,
        timeout: float = 30.0
    ) -> None:
        if agent_dir is None:
            agent_dir = get_agent_dir()
        
        self._global_path = Path(agent_dir) / "mounts.json"
        self._project_path = Path(cwd) / CONFIG_DIR_NAME / "mounts.json"
        self._lock = threading.Lock()
        self.timeout = timeout
    
    def with_lock(
        self,
        scope: MountScope,
        fn: Callable[[Optional[str]], Optional[str]]
    ) -> None:
        """Execute function with file lock."""
        path = (
            self._global_path 
            if scope == MountScope.GLOBAL 
            else self._project_path
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