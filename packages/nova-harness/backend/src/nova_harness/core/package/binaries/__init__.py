"""框架自管理二进制（pin 注册表 + 下载安装）。"""

from nova_harness.core.package.binaries.manager import (
    detect_platform_key,
    ensure_binary,
    is_offline_mode_enabled,
    list_managed_binaries,
)

__all__ = [
    "detect_platform_key",
    "ensure_binary",
    "is_offline_mode_enabled",
    "list_managed_binaries",
]
