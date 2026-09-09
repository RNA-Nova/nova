"""默认 AuthContext 实现。

对齐 TypeScript ``src/auth/context.ts``：从环境变量和本地文件系统读取信息。
"""

import os
from pathlib import Path
from typing import Optional

from ..types.auth import AuthContext


class DefaultAuthContext(AuthContext):
    """默认鉴权上下文：读 ``os.environ`` 与本地文件。"""

    async def env(self, name: str) -> Optional[str]:
        value = os.environ.get(name)
        if isinstance(value, str) and value.strip():
            return value
        return None

    async def file_exists(self, path: str) -> bool:
        try:
            resolved = Path(path).expanduser()
            return resolved.exists()
        except Exception:
            return False


def default_provider_auth_context() -> AuthContext:
    """构造默认 AuthContext。"""
    return DefaultAuthContext()


__all__ = [
    "DefaultAuthContext",
    "default_provider_auth_context",
]
