"""作用域化 UIContext：把当前作用域归属织入每次请求。

职责（仲裁时代的定位）：扩展 handler/工具的 ui 调用不主动声明归属——
本类在**注入点**把当前作用域（``run:<id>`` / ``session:<id>``）织入每个
request，归属进路由层台账。宿主死亡（run 终结/会话替换）时仲裁按归属
批量终结在飞请求（协程 cancelled + ``ui/cancel`` 撤框）——调用点零自觉
负担。

- 归属在**每次请求时**现取（run 进行中才有 run 归属；idle 时落 session
  归属——run 外的会话级交互不被 run 终结误杀）；
- ``capabilities`` / ``has_capability`` / ``notify`` 原样透传。

``request_lock``（工具执行面）：一轮内多个工具调用并行执行时，弹窗请求
必须串行——两个工具同时弹窗即界面打架。锁由会话级持有、每次
``get_tool_exec_context()`` 注入同一把。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Optional, Set

from nova_harness.core.types.ui.context import UIContext
from nova_harness.core.types.ui.primitives import UIResponse


class ScopedUIContext(UIContext):
    """把作用域归属织入每次 request 的 UIContext 包装器。

    ``scope_getter`` 在**每次请求时**现取（run 归属按 run 生死变化，
    不能构造期快照）；``request_lock`` 可选，给并行执行面（工具调用）
    做弹窗串行化。
    """

    def __init__(
        self,
        base: UIContext,
        scope_getter: Callable[[], Optional[str]],
        request_lock: Optional[asyncio.Lock] = None,
    ) -> None:
        self._base = base
        self._scope_getter = scope_getter
        self._request_lock = request_lock

    @property
    def capabilities(self) -> Set[str]:
        return self._base.capabilities

    async def request(
        self, method: str, params: Dict[str, Any], *, scope: Optional[str] = None
    ) -> UIResponse:
        # 调用方显式 scope 优先，否则用作用域注入的当前归属（现取）
        effective_scope = scope if scope is not None else self._scope_getter()
        lock = self._request_lock
        if lock is None:
            return await self._base.request(method, params, scope=effective_scope)
        async with lock:
            return await self._base.request(method, params, scope=effective_scope)

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        self._base.notify(method, params)


__all__ = ["ScopedUIContext"]
