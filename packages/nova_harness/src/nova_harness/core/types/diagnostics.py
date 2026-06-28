"""
通用诊断类型。

原位于 runtime/agent/diagnostics.py，为打破资源层对运行时层的依赖而上提到 types。
"""

from dataclasses import dataclass
from typing import Literal, Optional

from nova_ai.types.base_model import NovaBaseModel


@dataclass
class AgentSessionRuntimeDiagnostic:
    """运行时的非致命诊断条目。"""

    type: Literal["info", "warning", "error"]
    message: str


class ResourceCollision(NovaBaseModel):
    """描述两个资源之间的冲突。

    当相同名称的资源来自不同来源时发生。
    """

    resource_type: Literal["extension", "skill", "prompt", "theme"]
    name: str
    winner_path: str
    loser_path: str
    winner_source: Optional[str] = None
    loser_source: Optional[str] = None


class ResourceDiagnostic(NovaBaseModel):
    """资源诊断信息。

    用于报告资源加载或验证过程中的问题。
    """

    category: Literal["warning", "error", "collision"]  # 对应 TypeScript 的 type 字段
    message: str
    path: Optional[str] = None
    collision: Optional[ResourceCollision] = None
