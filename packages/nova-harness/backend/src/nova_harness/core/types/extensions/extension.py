"""已加载扩展对象类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.types.extensions.commands import (
    ExtensionFlag,
    ExtensionShortcut,
    RegisteredCommand,
)
from nova_harness.core.types.extensions.source import SourceInfo


@dataclass
class Extension:
    """已加载的扩展对象。"""

    path: str
    resolved_path: Optional[str] = None
    name: Optional[str] = None
    source_info: SourceInfo = field(
        default_factory=lambda: SourceInfo(path="", source="extension")
    )
    handlers: Dict[str, List[Callable[..., Any]]] = field(default_factory=dict)
    commands: Dict[str, RegisteredCommand] = field(default_factory=dict)
    flags: Dict[str, ExtensionFlag] = field(default_factory=dict)
    shortcuts: Dict[str, ExtensionShortcut] = field(default_factory=dict)


__all__ = ["Extension"]
