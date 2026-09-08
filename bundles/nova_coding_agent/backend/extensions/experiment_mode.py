"""experiment_mode 扩展（个人特色功能模式，骨架）。

当前仅注册 ``/experiment`` 命令做模式开关，尚无实际功能。
后续功能挂在 ``extension()`` 内的对应事件钩子与状态字段上（参照
plan_mode.py 的组织方式）。
"""

from __future__ import annotations

from typing import Any, Dict

from nova_base.ui_primitives import notify_message

from nova_harness.core.extensions.api import NovaExtensionAPI


def extension(nova: NovaExtensionAPI) -> None:
    """注册 experiment_mode 扩展。"""
    # 扩展实例闭包状态（后续功能的状态字段加在这里）
    state: Dict[str, Any] = {
        "enabled": False,
    }

    def _notify(ctx: Any, message: str, level: str = "info") -> None:
        if ctx.has_ui:
            notify_message(ctx.ui, message, level)

    def _toggle(ctx: Any) -> None:
        state["enabled"] = not state["enabled"]
        if state["enabled"]:
            _notify(ctx, "Experiment mode enabled.")
        else:
            _notify(ctx, "Experiment mode disabled.")

    async def _cmd_experiment(args: str, ctx: Any) -> None:
        _toggle(ctx)

    nova.registerCommand(
        "experiment",
        {
            "description": "切换 experiment 模式（个人特色功能）",
            "handler": _cmd_experiment,
        },
    )
