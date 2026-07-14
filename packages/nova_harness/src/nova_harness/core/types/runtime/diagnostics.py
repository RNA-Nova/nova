"""运行时诊断类型。"""

from dataclasses import dataclass
from typing import Literal


@dataclass
class AgentSessionRuntimeDiagnostic:
    """运行时的非致命诊断条目。"""

    type: Literal["info", "warning", "error"]
    message: str


__all__ = ["AgentSessionRuntimeDiagnostic"]
