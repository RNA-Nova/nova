"""Bash 执行相关类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol


@dataclass
class BashResult:
    """Bash 命令执行结果。"""

    output: str
    exit_code: int
    cancelled: bool = False
    truncated: bool = False
    full_output_path: Optional[str] = None


@dataclass
class BashSpawnContext:
    """Bash 子进程启动前的上下文，spawn hook 可修改。"""

    command: str
    cwd: str
    env: Dict[str, str]


BashSpawnHook = Callable[[BashSpawnContext], BashSpawnContext]
"""在启动子进程前调整 command/cwd/env 的钩子。"""


class BashOperations(Protocol):
    """Bash 执行后端协议（本地子进程、远程主机等）。"""

    async def execute(
        self,
        command: str,
        cwd: str,
        options: Dict[str, Any],
    ) -> BashResult:
        """执行命令并返回结果。"""
        ...


__all__ = [
    "BashResult",
    "BashOperations",
    "BashSpawnContext",
    "BashSpawnHook",
]
