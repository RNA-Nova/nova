"""NovaServer 共享状态。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from nova_harness.core.types.ui import NoOpUIContext
from nova_harness.core.types.ui.context import UIContext


@dataclass
class ServerState:
    """跨 JSON-RPC 方法共享的可变状态。"""

    runtime: Any = None
    ui_context: UIContext = field(default_factory=NoOpUIContext)
    on_runtime_created: Optional[Callable[[Any], None]] = None
    # 无会话时供 settings 域使用的懒加载管理器（避免每次调用新建后台写线程）
    fallback_settings_manager: Any = None
    # 事件序号发号器（单调递增，服务器生命周期内不回绕）——syncSession 的
    # 高水位锚点与前端增量丢弃的依据；broadcast_event 是唯一写入点
    event_seq: int = 0

    async def dispose_runtime(self) -> None:
        """释放当前 runtime（兼容同步/异步 dispose 实现）。"""
        if self.runtime is not None:
            result = self.runtime.dispose()
            if inspect.isawaitable(result):
                await result
            self.runtime = None

    def set_runtime(self, runtime: Any) -> None:
        self.runtime = runtime
        if self.on_runtime_created is not None:
            self.on_runtime_created(runtime)


__all__ = ["ServerState"]
