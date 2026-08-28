"""Provider-scoped environment variable 读取。

对齐 TypeScript ``src/utils/provider-env.ts``：
provider 可通过 ``providerEnv`` 配置覆盖进程环境变量。
"""

import os
from typing import Optional


def get_provider_env_value(
    name: str, provider_env: Optional[dict] = None
) -> Optional[str]:
    """按优先级读取 provider-scoped 环境变量。

    1. ``provider_env[name]``（如果传入）
    2. ``os.environ[name]``
    """
    if provider_env:
        value = provider_env.get(name)
        if isinstance(value, str) and value:
            return value
    value = os.environ.get(name)
    if isinstance(value, str) and value:
        return value
    return None


__all__ = ["get_provider_env_value"]
