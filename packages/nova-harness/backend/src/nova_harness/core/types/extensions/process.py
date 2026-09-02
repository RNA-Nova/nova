"""子进程启动 hook 契约类型（扩展 API 面）。

框架不认识任何具体工具，但"启动子进程"是 OS 级抽象——扩展可以通过
``registerSpawnHook`` 注册 hook 拦截类 shell 子进程的启动（由具体工具
实现消费——如 nova_coding_agent 的 bash 工具与会话 bash；将来 python、
docker 等 spawn 类工具实现同一协议即可接入）。本模块是这一扩展点
的契约，不包含任何执行实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Protocol


@dataclass
class SpawnContext:
    """子进程启动前的上下文，spawn hook 可修改。"""

    command: str
    cwd: str
    env: Dict[str, str]


SpawnHook = Callable[[SpawnContext], SpawnContext]
"""在启动子进程前调整 command/cwd/env 的钩子。"""


class SpawnHookAware(Protocol):
    """支持外部注入 spawn hook 的工具执行体。

    ``ToolsManager`` 在刷新工具注册表时，会把扩展注册的 spawn hooks
    聚合后注入实现本协议的执行体。
    """

    def set_spawn_hook(self, hook: Optional[SpawnHook]) -> None: ...


__all__ = [
    "SpawnContext",
    "SpawnHook",
    "SpawnHookAware",
]
