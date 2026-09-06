"""subagent 自治权检查点（subagent gate）。

设计定案 §7：执行前确认是**自治权检查点**（非注入安全）——委派动作会
fork 出一个具备独立能力面的子会话，用户在首次委派给某个 agent 前应有
发言权。拦截 ``subagent`` 工具调用，逐名裁决将执行的 agent：

- **允许一次**：本次放行，下次仍问；
- **本会话始终允许**：写入会话级允许集，``append_entry("subagent_allow",
  {"agents": [...]})`` 持久化（累计全集、合并去重；分支安全——
  session_start / session_tree 从分支最新条目恢复，tool-panel 模式）。
  跨会话"永远"不做——那是 trust.json 的换名；
- **取消**：拦截本次调用（``block=True``——reason 回给 LLM，模型可改道）。

headless（无 UI）直接放行——确认是有 UI 时的增强，不是 headless 新门槛。
多 agent 调用（parallel/chain）逐名裁决，任一取消即整体拦截。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from nova_base.ui_primitives import select

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.types.events.results import ToolCallEventResult

_CHOICE_ONCE = "允许一次"
_CHOICE_ALWAYS = "本会话始终允许"
_CHOICE_CANCEL = "取消"


def _extract_agent_names(args: Dict[str, Any]) -> List[str]:
    """从 subagent 调用参数提取将执行的 agent 名集合（三模式，去重保序）。"""
    names: List[str] = []
    seen: Set[str] = set()

    def _add(raw: Any) -> None:
        if isinstance(raw, str) and raw and raw not in seen:
            seen.add(raw)
            names.append(raw)

    _add(args.get("agent"))  # single
    for item in args.get("tasks") or []:  # parallel
        if isinstance(item, dict):
            _add(item.get("agent"))
    for item in args.get("chain") or []:  # chain
        if isinstance(item, dict):
            _add(item.get("agent"))
    return names


def extension(nova: NovaExtensionAPI) -> None:
    """注册 subagent tool_call 拦截与允许集的分支恢复。"""
    # 会话级 always 允许集（闭包状态；条目持久化 + 分支恢复）
    allowed_agents: Set[str] = set()

    def _latest_allowed(ctx: Any) -> Optional[Set[str]]:
        """扫当前分支取最新一条 subagent_allow 条目的允许集（无条目 None）。

        条目写的就是累计全集（每次 always 合并去重后全量落盘），故恢复
        语义为**替换**——分支/树导航后所见即该历史点的允许集。
        """
        sm = ctx.session_manager
        if sm is None:
            return None
        for entry in reversed(sm.get_branch()):
            if getattr(entry, "type", "") != "custom":
                continue
            if getattr(entry, "custom_type", "") != "subagent_allow":
                continue
            data = getattr(entry, "data", None)
            if isinstance(data, dict) and isinstance(data.get("agents"), list):
                return {str(a) for a in data["agents"]}
            return set()
        return None

    async def _restore_from_branch(event: Any, ctx: Any) -> None:
        """session_start / session_tree：有条目则替换允许集，无则不动。"""
        saved = _latest_allowed(ctx)
        if saved is None:
            return
        allowed_agents.clear()
        allowed_agents.update(saved)

    async def _on_tool_call(event: Any, ctx: Any) -> Optional[ToolCallEventResult]:
        if event.tool_name != "subagent":
            return None
        args = event.args if isinstance(event.args, dict) else {}
        names = _extract_agent_names(args)
        if not names:
            return None
        # headless 不设卡：确认是有 UI 时的增强，不是 headless 新门槛
        if not ctx.has_ui:
            return None

        for name in names:
            if name in allowed_agents:
                continue
            choice = await select(
                ctx.ui,
                f"⚠️ 即将委派任务给 agent “{name}”（独立子会话执行）。允许？",
                [_CHOICE_ONCE, _CHOICE_ALWAYS, _CHOICE_CANCEL],
            )
            if choice == _CHOICE_ONCE:
                continue
            if choice == _CHOICE_ALWAYS:
                allowed_agents.add(name)
                # 累计全集落盘（合并去重）——恢复取最新一条即全量
                ctx.append_entry("subagent_allow", {"agents": sorted(allowed_agents)})
                continue
            # 取消（含选择器被 Esc）——拦截本次调用
            return ToolCallEventResult(
                block=True,
                reason=f"Subagent delegation to '{name}' cancelled by user",
            )
        return None

    nova.on("tool_call", _on_tool_call)
    nova.on("session_start", _restore_from_branch)
    nova.on("session_tree", _restore_from_branch)
