"""压缩与分支摘要共用的基础类型。"""

from typing import Set

from nova_ai.types.base_model import NovaBaseModel
from pydantic import Field


class FileOperations(NovaBaseModel):
    """Track file operations during agent execution."""

    read: Set[str] = Field(default_factory=set)
    written: Set[str] = Field(default_factory=set)
    edited: Set[str] = Field(default_factory=set)


__all__ = ["FileOperations"]
