"""扩展 exec 命令相关类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ExecOptions:
    """扩展 exec 命令选项。"""

    cwd: Optional[str] = None
    timeout: Optional[float] = None
    env: Optional[Dict[str, str]] = None


@dataclass(frozen=True)
class ExecResult:
    """扩展 exec 命令结果。"""

    stdout: str = ""
    stderr: str = ""
    code: int = 0
    killed: bool = False


__all__ = ["ExecOptions", "ExecResult"]
