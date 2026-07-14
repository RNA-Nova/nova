"""Project Trust 类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, List, Literal, Optional

from nova_harness.core.types.ui import ExtensionMode, UIContext

if TYPE_CHECKING:
    from nova_harness.core.harness.project_trust.trust_store import ProjectTrustStore

DefaultProjectTrust = Literal["always", "never", "ask"]


@dataclass
class ProjectTrustEvent:
    """允许扩展参与项目信任决策的事件。"""

    type: str = "project_trust"
    cwd: str = ""


@dataclass
class ProjectTrustEventResult:
    """扩展对 project_trust 事件的响应。"""

    trusted: Literal["yes", "no", "undecided"] = "no"
    remember: bool = False


@dataclass
class ProjectTrustUpdate:
    """一条信任记录更新。"""

    path: str
    decision: Optional[bool]


@dataclass
class ProjectTrustOption:
    """展示给用户的信任选项。"""

    label: str
    trusted: bool
    updates: List[ProjectTrustUpdate] = field(default_factory=list)
    saved_path: Optional[str] = None


@dataclass
class ProjectTrustContext:
    """project_trust 事件处理器可用的上下文。"""

    cwd: str
    mode: ExtensionMode
    has_ui: bool
    ui: UIContext


@dataclass
class ResolveProjectTrustedOptions:
    """resolve_project_trusted 的选项。"""

    cwd: str
    trust_store: "ProjectTrustStore"
    trust_override: Optional[bool] = None
    default_project_trust: DefaultProjectTrust = "ask"
    extensions_result: Optional[Any] = None
    project_trust_context: Optional[ProjectTrustContext] = None
    on_extension_error: Optional[Callable[[str], None]] = None


class ProjectNotTrustedError(PermissionError):
    """项目未通过 trust 校验，但操作需要写入 project scope 时抛出。"""

    def __init__(self, cwd: str = "") -> None:
        msg = (
            "Project scope is not trusted. "
            f"Run 'nova-pkg trust' in '{cwd}' to enable project-level package operations."
        )
        super().__init__(msg)
        self.cwd = cwd


__all__ = [
    "DefaultProjectTrust",
    "ProjectTrustEvent",
    "ProjectTrustEventResult",
    "ProjectTrustUpdate",
    "ProjectTrustOption",
    "ProjectTrustContext",
    "ProjectNotTrustedError",
    "ResolveProjectTrustedOptions",
]
