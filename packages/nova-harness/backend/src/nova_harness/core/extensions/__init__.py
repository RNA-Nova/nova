"""Nova Harness 扩展系统公共入口。

对外暴露 ``ExtensionRunner``、``load_extensions``、``NovaExtensionAPI`` 等核心能力。
"""

from nova_harness.core.extensions.api import NovaExtensionAPI, create_extension_api
from nova_harness.core.extensions.loader import (
    ExtensionLoader,
    ExtensionLoadError,
    load_extensions,
)
from nova_harness.core.extensions.runner import (
    ExtensionRunner,
    emit_project_trust_event,
    emit_session_shutdown_event,
)

__all__ = [
    "ExtensionRunner",
    "ExtensionLoader",
    "ExtensionLoadError",
    "NovaExtensionAPI",
    "create_extension_api",
    "emit_project_trust_event",
    "emit_session_shutdown_event",
    "load_extensions",
]
