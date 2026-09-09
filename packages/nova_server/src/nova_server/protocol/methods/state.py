"""NovaServer 共享状态。"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from nova_harness.core.runtime_manager import RuntimeManager
from nova_harness.types.ui import NoOpUIContext
from nova_harness.types.ui.context import UIContext


@dataclass
class ServerState:
    """跨 JSON-RPC 方法共享的可变状态。

    会话创建/注销统一走 ``runtime_manager``（对位 codex ThreadManager）；
    ``runtime`` 只是"当前会话"哑指针（session.py 内 95 处读依赖它）。
    """

    runtime: Any = None
    # 进程级会话工厂 + 注册表；createSession 经 open_session，注销经
    # close_runtime（按身份，switch/fork 原地换会后 id 漂移安全）
    runtime_manager: Any = field(default_factory=RuntimeManager)
    ui_context: UIContext = field(default_factory=NoOpUIContext)
    on_runtime_created: Optional[Callable[[Any], None]] = None
    # 无会话时供 settings 域使用的懒加载管理器（避免每次调用新建后台写线程）
    fallback_settings_manager: Any = None
    # 事件序号发号器（单调递增，服务器生命周期内不回绕）——syncSession 的
    # 高水位锚点与前端增量丢弃的依据；broadcast_event 是唯一写入点
    event_seq: int = 0
    # 当前会话的归约器（server 在会话订阅时写入）——syncSession 拼在飞段用
    item_reducer: Any = None

    async def dispose_runtime(self) -> None:
        """释放当前 runtime：优先经注册表注销；非托管实例（测试假件）
        回退到兼容同步/异步 dispose 的直接释放。"""
        if self.runtime is None:
            return
        closed = False
        closer = getattr(self.runtime_manager, "close_runtime", None)
        if closer is not None:
            closed = await closer(self.runtime)
        if not closed:
            result = self.runtime.dispose()
            if inspect.isawaitable(result):
                await result
        self.runtime = None

    def set_runtime(self, runtime: Any) -> None:
        self.runtime = runtime
        if self.on_runtime_created is not None:
            self.on_runtime_created(runtime)


__all__ = ["ServerState"]
