"""AgentSession 运行时诊断与问题描述类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class AgentSessionRuntimeDiagnostic:
    """运行时的非致命诊断条目。"""

    type: Literal["info", "warning", "error"]
    message: str


@dataclass(frozen=True)
class SessionCwdIssue:
    """缺失会话 cwd 的问题描述。"""

    session_file: Optional[str]
    session_cwd: str
    fallback_cwd: str


__all__ = ["AgentSessionRuntimeDiagnostic", "SessionCwdIssue"]
