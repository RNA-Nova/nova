"""离线模式检测。

支持 ``NOVA_OFFLINE`` 环境变量开启离线模式。
"""

import os

OFFLINE_TRUTHY = {"1", "true", "yes"}


def is_offline_mode_enabled() -> bool:
    """Return True when offline mode is enabled via ``NOVA_OFFLINE``."""
    return os.environ.get("NOVA_OFFLINE", "").lower() in OFFLINE_TRUTHY


__all__ = ["is_offline_mode_enabled"]
