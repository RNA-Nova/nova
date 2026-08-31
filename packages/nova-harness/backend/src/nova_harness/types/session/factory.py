"""AgentSession Runtime 工厂返回类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, List, Optional

from nova_harness.core.types.extensions import LoadedExtensionsResult
from nova_harness.core.types.project_trust import ProjectTrustContext
from nova_harness.core.types.protocols import (
    AgentSessionProtocol,
    AgentSessionServicesProtocol,
    SessionManagerProtocol,
)
from nova_harness.core.types.session.diagnostics import AgentSessionRuntimeDiagnostic

if TYPE_CHECKING:
    # 仅注解引用（dataclass 字段，运行时不求值）；types.events 的包级
    # re-export 链会经 types.compaction 回到 types.session，形成循环
    # import，因此只在 TYPE_CHECKING 下导入。
    from nova_harness.core.types.events.session import SessionStartEvent


@dataclass
class CreateAgentSessionResult:
    """创建 AgentSession 的返回结果。"""

    session: AgentSessionProtocol
    extensions_result: LoadedExtensionsResult
    model_fallback_message: Optional[str] = None


@dataclass
class CreateAgentSessionRuntimeOptions:
    """Runtime 工厂接收的选项对象。"""

    cwd: str
    agent_dir: str
    session_manager: SessionManagerProtocol
    session_start_event: Optional[SessionStartEvent] = None
    project_trust_context: Optional[ProjectTrustContext] = None


@dataclass
class CreateAgentSessionRuntimeResult(CreateAgentSessionResult):
    """Runtime 工厂返回的结果，在 CreateAgentSessionResult 基础上增加 services 与 diagnostics。"""

    services: AgentSessionServicesProtocol = field(default=None)
    diagnostics: List[AgentSessionRuntimeDiagnostic] = field(default_factory=list)


CreateAgentSessionRuntimeFactory = Callable[
    [CreateAgentSessionRuntimeOptions],
    Awaitable[CreateAgentSessionRuntimeResult],
]


__all__ = [
    "CreateAgentSessionResult",
    "CreateAgentSessionRuntimeFactory",
    "CreateAgentSessionRuntimeOptions",
    "CreateAgentSessionRuntimeResult",
]
