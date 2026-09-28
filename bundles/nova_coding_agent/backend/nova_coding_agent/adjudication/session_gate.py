"""会话切换裁决点（confirm_destructive 收编）。

订阅 ``session_before_switch``（reason: "new"|"resume"）与
``session_before_fork``：离开当前会话前弹确认。语义与委派点同型——
headless/空会话放行，有 UI 且非空会话经 confirm 询问，用户选否/取消
返回类型化结果（``cancel=True``，runtime 读 ``result.cancel`` 取消切换）。

这是自治权检查点（非安全边界）：与 bash 裁决的 fail-closed 刻意相反。
"""

from __future__ import annotations

from typing import Any, Optional

from nova_coding_agent.ui_primitives import confirm
from nova_harness.events.results import (
    SessionBeforeForkResult,
    SessionBeforeSwitchResult,
)

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


async def on_before_switch(
    event: Any, ctx: Any
) -> Optional[SessionBeforeSwitchResult]:
    action = _ACTION_LABELS.get(getattr(event, "reason", ""), "切换会话")
    if await _confirm_leave(ctx, action):
        return None
    return SessionBeforeSwitchResult(cancel=True)


async def on_before_fork(event: Any, ctx: Any) -> Optional[SessionBeforeForkResult]:
    if await _confirm_leave(ctx, "分叉会话"):
        return None
    return SessionBeforeForkResult(cancel=True)
