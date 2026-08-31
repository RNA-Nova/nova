"""压缩与分支摘要共用的基础类型。"""

from dataclasses import dataclass, field
from typing import Set


@dataclass
class FileOperations:
    """Track file operations during agent execution.

    压缩流程中的**可变累加器**（``file_ops.read.add(...)``），按数据建模
    规则 1（可变容器禁用 Pydantic）使用 dataclass。
    """

    read: Set[str] = field(default_factory=set)
    written: Set[str] = field(default_factory=set)
    edited: Set[str] = field(default_factory=set)


__all__ = ["FileOperations"]
