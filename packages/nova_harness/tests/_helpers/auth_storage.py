"""AuthStorage 内存工厂测试辅助函数。"""

import json
from typing import Any, Dict, Optional

from nova_harness.config.auth.storage import AuthStorage
from nova_harness.config.storage import InMemoryStorageBackend

AuthStorageData = Dict[str, Dict[str, Any]]


def auth_storage_in_memory(data: Optional[AuthStorageData] = None) -> AuthStorage:
    """Create in-memory AuthStorage for testing."""
    if data is None:
        data = {}
    storage = InMemoryStorageBackend(json.dumps(data, indent=2))
    return AuthStorage.from_storage(storage)
