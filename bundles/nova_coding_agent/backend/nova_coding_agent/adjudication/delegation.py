"""委派裁决点（subagent_gate 收编——自治权检查点，非注入安全）。

委派动作会 fork 出独立能力面的子会话——用户在首次委派给某 agent 前应有
发言权。**headless 直接放行**（确认是有 UI 时的增强，不是新门槛——与
bash 裁决的 fail-closed 语义刻意相反，自治权检查点不是安全边界）。

三结局（对位 ReviewDecision 的 autonomy 变体）：

- 允许一次：本次放行，下次仍问；
- 本会话始终允许：会话级允许集 + ``subagent_allow`` 条目持久化
  （累计全集、分支恢复取最新一条——恢复即替换语义）；
- 取消：拦截本次调用（block=True，reason 回给 LLM）。

多 agent 调用（parallel/chain）逐名裁决，任一取消即整体拦截。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from nova_coding_agent.ui_primitives import select
from nova_harness.events.results import ToolCallEventResult

_CHOICE_ONCE = "允许一次"
_CHOICE_ALWAYS = "本会话始终允许"
_CHOICE_CANCEL = "取消"

_ENTRY_TYPE = "subagent_allow"


def extract_agent_names(args: Dict[str, Any]) -> List[str]:
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


class DelegationGate:
    """委派检查点（扩展实例生命周期持有允许集——与 ApprovalFlow 同形态）"""

    def __init__(self) -> None:
        self._allowed: Set[str] = set()

    def latest_allowed(self, ctx: Any) -> Optional[Set[str]]:
        """扫当前分支取最新一条 subagent_allow 条目的允许集（无条目 None）。"""
        sm = ctx.session_manager
        if sm is None:
            return None
        for entry in reversed(sm.get_branch()):
            if getattr(entry, "type", "") != "custom":
                continue
            if getattr(entry, "custom_type", "") != _ENTRY_TYPE:
                continue
            data = getattr(entry, "data", None)
            if isinstance(data, dict) and isinstance(data.get("agents"), list):
                return {str(a) for a in data["agents"]}
            return set()
        return None

    def restore(self, ctx: Any) -> None:
        """session_start / session_tree：有条目则替换允许集，无则不动。"""
        saved = self.latest_allowed(ctx)
        if saved is None:
            return
        self._allowed.clear()
        self._allowed.update(saved)

    async def on_tool_call(self, event: Any, ctx: Any) -> Optional[ToolCallEventResult]:
        if event.tool_name != "subagent":
            return None
        args = event.args if isinstance(event.args, dict) else {}
        names = extract_agent_names(args)
        if not names:
            return None
        # headless 不设卡：确认是有 UI 时的增强，不是 headless 新门槛
        if not ctx.has_ui:
            return None

        for name in names:
            if name in self._allowed:
                continue
            choice = await select(
                ctx.ui,
                f"⚠️ 即将委派任务给 agent “{name}”（独立子会话执行）。允许？",
                [_CHOICE_ONCE, _CHOICE_ALWAYS, _CHOICE_CANCEL],
            )
            if choice == _CHOICE_ONCE:
                continue
            if choice == _CHOICE_ALWAYS:
                self._allowed.add(name)
                # 累计全集落盘（合并去重）——恢复取最新一条即全量
                ctx.append_entry(
                    _ENTRY_TYPE, {"agents": sorted(self._allowed)}
                )
                continue
            # 取消（含选择器被 Esc）——拦截本次调用
            return ToolCallEventResult(
                block=True,
                reason=f"Subagent delegation to '{name}' cancelled by user",
            )
        return None
