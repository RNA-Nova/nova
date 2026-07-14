"""资源加载诊断类型。"""

from typing import Literal, Optional

from nova_ai.types.base_model import NovaBaseModel


class ResourceCollision(NovaBaseModel):
    """描述两个资源之间的冲突。

    当相同名称的资源来自不同来源时发生。
    """

    resource_type: Literal["extension", "skill", "prompt", "theme", "tool", "agent"]
    name: str
    winner_path: str
    loser_path: str
    winner_source: Optional[str] = None
    loser_source: Optional[str] = None


class ResourceDiagnostic(NovaBaseModel):
    """资源诊断信息。

    用于报告资源加载或验证过程中的问题。
    """

    category: Literal["warning", "error", "collision"]  # 诊断类别
    message: str
    path: Optional[str] = None
    collision: Optional[ResourceCollision] = None


__all__ = ["ResourceCollision", "ResourceDiagnostic"]
