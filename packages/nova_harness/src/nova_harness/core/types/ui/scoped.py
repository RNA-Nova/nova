"""作用域化 UIContext：自动把 abort signal 注入每次请求。

解决的问题：扩展 handler 的 ui 调用（``ui.request("select", ...)``）不主动
传 signal——turn 被 abort 时（用户 Esc），挂在对话框上的 await 会一直挂到
应答或超时。本类在**注入点**把当前 run 的 abort signal 织入每个 request：

- 请求发起时现取 signal（run 进行中才有；idle 时为 None，不竞速）；
- abort 胜出 → 底层 transport 发 ``ui/cancel`` 撤销前端对话框 + 按
  cancelled 解决——扩展 handler 无需任何 signal 处理代码；
- 调用方显式传 signal 时**显式优先**（域级信号经事件荷载显式传入，
  压过 run 默认——绑定决策权归真正知情的一方）；
- ``capabilities`` / ``has_capability`` / ``notify`` 原样透传。

可选 ``request_lock``（工具执行面）：一轮内多个工具调用并行执行时，
弹窗请求必须串行——两个工具同时弹窗即界面打架。锁由会话级持有、
每次 ``get_tool_exec_context()`` 注入同一把；等锁期间 run abort 的，
锁释放后请求携已 abort 的 signal 由 transport 立即按 cancelled 解决
（自愈）；**已 abort 的请求不参与排队**（预检直放，不白等别人的对话框）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Set

from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse


class ScopedUIContext(UIContext):
    """把 abort signal 织入每次 request 的 UIContext 包装器。

    ``signal_getter`` 在**每次请求时**现取（run 的 AbortController 按 run
    创建/清空，不能构造期快照）；``request_lock`` 可选，给并行执行面
    （工具调用）做弹窗串行化。
    """

    def __init__(
        self,
        base: UIContext,
        signal_getter: Callable[[], Optional[Any]],
        request_lock: Optional[asyncio.Lock] = None,
    ) -> None:
        self._base = base
        self._signal_getter = signal_getter
        self._request_lock = request_lock

    @property
    def capabilities(self) -> Set[str]:
        return self._base.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], signal: Any = None
    ) -> UIResponse:
        # 调用方显式 signal 优先，否则用作用域 signal（当前 run 的 abort 信号）
        effective = signal if signal is not None else self._signal_getter()
        lock = self._request_lock
        if lock is None:
            return await self._base.request(method, params, effective)
        # 已 abort 的请求不排队——直接交给 transport 按 cancelled 解决
        if effective is not None and getattr(effective, "aborted", False):
            return await self._base.request(method, params, effective)
        async with lock:
            return await self._base.request(method, params, effective)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._base.notify(method, params)


__all__ = ["ScopedUIContext"]
