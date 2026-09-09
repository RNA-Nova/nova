"""扩展协议类型统一入口。

所有扩展相关类型均定义在本子包内，业务模块统一从 ``nova_harness.types.extensions`` 导入。
"""

from nova_harness.types.extensions.actions import (
    ExtensionActions,
    ExtensionCommandContextActions,
    ExtensionContextActions,
    ExtensionProviderActions,
)
from nova_harness.types.extensions.api import ExtensionAPI, ExtensionFactory
from nova_harness.types.extensions.commands import (
    ExtensionCommand,
    ExtensionFlag,
    ExtensionShortcut,
    RegisteredCommand,
    SlashCommandInfo,
    SlashCommandSource,
)
from nova_harness.types.extensions.context import (
    ExtensionCommandContext,
    ExtensionContext,
)
from nova_harness.types.extensions.exec import ExecOptions, ExecResult
from nova_harness.types.extensions.extension import Extension
from nova_harness.types.extensions.loading import LoadedExtensionsResult
from nova_harness.types.extensions.process import (
    SpawnContext,
    SpawnHook,
    SpawnHookAware,
)
from nova_harness.types.extensions.runtime import ExtensionRuntime
from nova_harness.types.extensions.source import SourceInfo
from nova_harness.types.package import SourceOrigin, SourceScope

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
    "RegisteredCommand",
    "SlashCommandInfo",
    "SlashCommandSource",
    "SourceInfo",
    "SourceOrigin",
    "SourceScope",
    "SpawnContext",
    "SpawnHook",
    "SpawnHookAware",
]
