"""confirm-destructive 扩展（pi examples/extensions/confirm-destructive.ts 对位）。

订阅 ``session_before_switch``（reason: "new"|"resume"）与
``session_before_fork``：离开当前会话前弹确认——当前会话有 N 条条目时
经 ``ui_primitives.confirm`` 询问，用户选否/取消 → 返回 ``cancel=True``
的**类型化结果**（runtime 读 ``result.cancel`` 取消本次切换；与
permission_gate 返回 ToolCallEventResult 同一惯用法——纯 dict 会被
runtime 的 ``getattr(result, "cancel")`` 读丢）。

- 条目数为 0（空会话）：不拦直接放行；
- headless（无 UI）：放行（不返回）。
"""

from __future__ import annotations

from typing import Any, Optional

from nova_coding_agent.ui_primitives import confirm

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.types.events.results import (
    SessionBeforeForkResult,
    SessionBeforeSwitchResult,
)

# before_switch reason → 动作文案（fork 走独立事件，文案固定）
_ACTION_LABELS = {"new": "新建会话", "resume": "切换会话"}


async def _confirm_leave(ctx: Any, action: str) -> bool:
    """离开确认：放行返回 True（空会话/headless/用户确认），拦截返回 False。"""
    sm = ctx.session_manager
    entry_count = len(sm.get_entries()) if sm is not None else 0
    if entry_count == 0 or not ctx.has_ui:
        return True
    return await confirm(
        ctx.ui,
        action,
        f"{action}将离开当前会话（{entry_count} 条条目）。继续？",
    )


async def _on_before_switch(
    event: Any, ctx: Any
) -> Optional[SessionBeforeSwitchResult]:
    action = _ACTION_LABELS.get(getattr(event, "reason", ""), "切换会话")
    if await _confirm_leave(ctx, action):
        return None
    return SessionBeforeSwitchResult(cancel=True)


async def _on_before_fork(event: Any, ctx: Any) -> Optional[SessionBeforeForkResult]:
    if await _confirm_leave(ctx, "分叉会话"):
        return None
    return SessionBeforeForkResult(cancel=True)


def extension(nova: NovaExtensionAPI) -> None:
    """注册 before_switch / before_fork 确认 handler。"""
    nova.on("session_before_switch", _on_before_switch)
    nova.on("session_before_fork", _on_before_fork)
