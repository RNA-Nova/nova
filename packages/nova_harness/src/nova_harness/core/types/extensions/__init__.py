"""扩展协议类型统一入口。

所有扩展相关类型均定义在本子包内，业务模块统一从 ``nova_harness.core.types.extensions`` 导入。
"""

from nova_harness.core.types.extensions.actions import (
    ExtensionActions,
    ExtensionCommandContextActions,
    ExtensionContextActions,
    ExtensionProviderActions,
)
from nova_harness.core.types.extensions.api import ExtensionAPI, ExtensionFactory
from nova_harness.core.types.extensions.commands import (
    ExtensionCommand,
    ExtensionFlag,
    ExtensionShortcut,
    RegisteredCommand,
    SlashCommandInfo,
    SlashCommandSource,
)
from nova_harness.core.types.extensions.context import (
    ExtensionCommandContext,
    ExtensionContext,
)
from nova_harness.core.types.extensions.exec import ExecOptions, ExecResult
from nova_harness.core.types.extensions.extension import Extension, MessageRenderer
from nova_harness.core.types.extensions.loading import (
    LoadedExtensionsResult,
    LoadExtensionsResult,
)
from nova_harness.core.types.extensions.runtime import ExtensionRuntime
from nova_harness.core.types.extensions.source import SourceInfo
from nova_harness.core.types.package_manager import SourceOrigin, SourceScope

__all__ = [
    "ExecOptions",
    "ExecResult",
    "Extension",
    "ExtensionActions",
    "ExtensionAPI",
    "ExtensionCommand",
    "ExtensionCommandContext",
    "ExtensionCommandContextActions",
    "ExtensionContext",
    "ExtensionContextActions",
    "ExtensionFactory",
    "ExtensionFlag",
    "ExtensionProviderActions",
    "ExtensionRuntime",
    "ExtensionShortcut",
    "LoadedExtensionsResult",
    "LoadExtensionsResult",
    "MessageRenderer",
    "RegisteredCommand",
    "SlashCommandInfo",
    "SlashCommandSource",
    "SourceInfo",
    "SourceOrigin",
    "SourceScope",
]
