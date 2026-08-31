"""tools 面板扩展（/tools 交互开关面板——pi examples/extensions/tools.ts 对位）。

- ``/tools`` 命令：``dialog:tools`` 已注册时弹包侧开关面板——应答
  ``{"active": [name...]}``（绝对集，与 ``set_active_tools`` 语义一致）
  → 应用 + ``append_entry("tool-panel")`` 持久化 + ``append_entry``
  确认条目（command_result 转录卡片，不进 LLM 上下文）；
  cancelled / 应答非法 → 无操作。无 UI 或无面板能力 → 文本清单回退
  （列出全部工具与激活状态）；
- 状态恢复（pi session_start/session_tree 对位）：订阅 ``session_start``
  与 ``session_tree``——扫当前分支最新 ``tool-panel`` 条目（同 /todos 的
  get_branch 扫描），有则应用 ``set_active_tools``，无则不动（默认全激活）。
  分支/树导航后所见即该历史点的开关集。

与 plan_mode 的交互：两者都会改写激活集，互不特判——**后动者胜**
（plan_mode 开关时本就会改激活集，面板随后应用即覆盖，反之亦然）。
"""

from __future__ import annotations

from typing import Any, List, Optional

from nova_harness.extensions.api import NovaExtensionAPI


def _reply(ctx: Any, text: str, level: str = "info") -> None:
    """命令反馈：转录卡片 + 持久化条目，不进 LLM 上下文。

    与 session_commands.py 的 ``_reply`` 同源（extensions 之间不互相 import，
    各保留一份拷贝）。
    """
    ctx.append_entry("command_result", {"text": text, "level": level})


def _current_role(ctx: Any) -> Optional[str]:
    """当前角色名（agent 注册表条目里 current 标记项）。"""
    try:
        for entry in ctx.get_agents() or []:
            if isinstance(entry, dict) and entry.get("current"):
                return str(entry.get("name") or "") or None
    except Exception:
        pass
    return None


def _latest_panel_active(ctx: Any) -> Optional[List[str]]:
    """扫当前分支取最新一条 tool-panel 条目的激活集（无则 None）。

    分支安全 + **角色归属**：条目带记录时的角色名，仅当条目角色与当前
    角色一致时才应用——面板 delta 属于角色上下文，不随 /agent 切换
    越界（旧角色的开关集不能盖到新角色的初始态上）。
    """
    sm = ctx.session_manager
    if sm is None:
        return None
    current_role = _current_role(ctx)
    for entry in reversed(sm.get_branch()):
        if getattr(entry, "type", "") != "custom":
            continue
        if getattr(entry, "custom_type", "") != "tool-panel":
            continue
        data = getattr(entry, "data", None)
        if isinstance(data, dict) and isinstance(data.get("active"), list):
            if data.get("role") != current_role:
                return None
            return [str(n) for n in data["active"]]
    return None


async def _restore_from_branch(ctx: Any) -> None:
    """session_start / session_tree：有条目则应用，无则不动（默认全激活）。"""
    active = _latest_panel_active(ctx)
    if active is not None:
        ctx.set_active_tools(active)


async def _cmd_tools(args: str, ctx: Any) -> None:
    tools = ctx.get_all_tools() or []
    active = set(ctx.get_active_tools() or [])

    if not ctx.has_ui or not ctx.ui.has_capability("dialog:tools"):
        # 无 UI / 无面板能力：文本清单回退（headless 保底形态）
        lines = [f"Tools — {len(active)}/{len(tools)} active", ""]
        for t in tools:
            name = t.get("name", "?")
            mark = "✓" if name in active else "✗"
            description = t.get("description") or ""
            lines.append(f"{mark} {name}  {description}".rstrip())
        _reply(ctx, "\n".join(lines))
        return

    resp = await ctx.ui.request(
        "dialog:tools",
        {
            "tools": [
                {
                    "name": t.get("name", ""),
                    "label": t.get("label") or t.get("name", ""),
                    "description": t.get("description") or "",
                    "active": t.get("name", "") in active,
                }
                for t in tools
            ]
        },
    )
    if resp.cancelled or not isinstance(resp.value, dict):
        return
    chosen = resp.value.get("active")
    if not isinstance(chosen, list):
        return
    names = [str(n) for n in chosen]
    ctx.set_active_tools(names)
    ctx.append_entry("tool-panel", {"active": names, "role": _current_role(ctx)})
    _reply(ctx, f"已更新激活工具集（{len(names)}/{len(tools)}）")


def extension(nova: NovaExtensionAPI) -> None:
    """注册 /tools 命令与分支状态恢复。"""
    nova.registerCommand(
        "tools",
        {
            "description": "查看与开关激活工具（TUI 下弹开关面板）",
            "handler": _cmd_tools,
        },
    )
    nova.on("session_start", lambda event, ctx: _restore_from_branch(ctx))
    nova.on("session_tree", lambda event, ctx: _restore_from_branch(ctx))
