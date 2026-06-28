"""
Nova extension system.

提供与 TypeScript `coding-agent` 类似的扩展能力：
- 事件订阅与拦截
- 工具/provider/命令/快捷键注册
- 压缩、树导航等会话生命周期 hook

扩展开发中需要的事件类型与常量，请从 `nova_harness.core.types.events` 导入。
"""

from nova_harness.core.agent_session.extensions.api import NovaExtensionAPI
from nova_harness.core.agent_session.extensions.runner import ExtensionRunner
from nova_harness.core.resources.loaders.extensions import (
    ExtensionLoader,
    ExtensionLoadError,
)
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionCommand,
    ExtensionFlag,
    ExtensionProviderRegistration,
    ExtensionShortcut,
    ExtensionToolDefinition,
)

__all__ = [
    "Extension",
    "ExtensionCommand",
    "ExtensionFlag",
    "ExtensionLoadError",
    "ExtensionLoader",
    "ExtensionRunner",
    "ExtensionProviderRegistration",
    "ExtensionShortcut",
    "ExtensionToolDefinition",
    "NovaExtensionAPI",
]
