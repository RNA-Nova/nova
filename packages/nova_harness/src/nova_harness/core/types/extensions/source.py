"""扩展/命令/工具来源信息类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class SourceInfo:
    """扩展/工具/命令的来源描述。"""

    path: str
    source: str = "extension"
    scope: Literal["user", "project", "temporary"] = "temporary"
    origin: Literal["package", "top-level", "local", "auto"] = "top-level"
    base_dir: Optional[str] = None


__all__ = ["SourceInfo"]
