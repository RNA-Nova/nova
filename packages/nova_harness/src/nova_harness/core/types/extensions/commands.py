"""扩展命令与 flag 类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from nova_harness.core.types.extensions.source import SourceInfo


def _noop_handler(*args: Any, **kwargs: Any) -> Any:
    """默认空 handler。"""
    return None


@dataclass
class RegisteredCommand:
    """扩展注册的命令。"""

    name: str
    description: Optional[str] = None
    source_info: SourceInfo = field(
        default_factory=lambda: SourceInfo(path="", source="extension")
    )
    handler: Callable[..., Any] = field(default_factory=lambda: _noop_handler)
    get_argument_completions: Optional[Callable[..., Any]] = None
    invocation_name: Optional[str] = None

    @property
    def extension_path(self) -> Optional[str]:
        return self.source_info.path

    @property
    def resolved_name(self) -> str:
        """返回实际调用名：优先使用自动重命名后的 invocation_name。"""
        return self.invocation_name or self.name


ExtensionCommand = RegisteredCommand


@dataclass
class ExtensionShortcut:
    """扩展注册的快捷键。

    键位捕获与绑定归前端；运行时持有注册表与 handler（执行体），
    前端经 ``getShortcuts`` 拿目录、``invokeShortcut`` 回调执行。
    """

    shortcut: str
    description: Optional[str] = None
    extension_path: Optional[str] = None
    handler: Callable[..., Any] = field(default_factory=lambda: _noop_handler)


@dataclass(frozen=True)
class ExtensionFlag:
    """扩展 flag。"""

    name: str
    type: Literal["boolean", "string", "number"] = "boolean"
    description: Optional[str] = None
    default: Any = None
    extension_path: Optional[str] = None


SlashCommandSource = Literal["extension", "prompt", "skill"]


@dataclass(frozen=True)
class SlashCommandInfo:
    """用于 UI 展示与命令分发的 slash 命令元数据。"""

    name: str
    source: SlashCommandSource = "extension"
    description: Optional[str] = None
    source_info: SourceInfo = field(
        default_factory=lambda: SourceInfo(path="", source="extension")
    )


@dataclass
class _ProviderRegistration:
    """Provider 注册排队项。"""

    name: str
    config: Any
    extension_path: Optional[str] = None


__all__ = [
    "RegisteredCommand",
    "ExtensionCommand",
    "ExtensionShortcut",
    "ExtensionFlag",
    "SlashCommandSource",
    "SlashCommandInfo",
    "_ProviderRegistration",
]
