"""扩展加载结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, List, Optional

from nova_harness.core.types.extensions.extension import Extension
from nova_harness.core.types.extensions.runtime import ExtensionRuntime

if TYPE_CHECKING:
    from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic


@dataclass
class LoadedExtensionsResult:
    """扩展加载结果。"""

    extensions: List[Extension] = field(default_factory=list)
    errors: List[Any] = field(default_factory=list)
    runtime: Optional[ExtensionRuntime] = None
    diagnostics: List["ResourceDiagnostic"] = field(default_factory=list)


# 历史别名：旧代码/文档使用 LoadExtensionsResult
LoadExtensionsResult = LoadedExtensionsResult


__all__ = ["LoadedExtensionsResult", "LoadExtensionsResult"]
