"""审批流：弹窗 + 会话缓存 + "永远允许"写回（对位 codex session.request_approval）。

顺序（对位审批流纪律）：

1. `approval_policy = "never"` 或无 UI → **fail-closed deny**（对位 codex：
   prompt 在 never 下视为拒绝；headless 无法完成询问）；
2. 本会话缓存命中（同一 title+command 精确签名）→ APPROVED_FOR_SESSION；
3. 弹窗四结局（对位 ReviewDecision）：
   - 允许一次 → APPROVED
   - 本会话允许 → 写缓存 + APPROVED_FOR_SESSION（条目持久化，分支恢复）
   - 永远允许 → RulesStore.append_amendment 写规则 + APPROVED_EXECPOLICY_AMENDMENT
   - 拒绝/取消 → DENIED（rejection 文案）

全部结局经 `_record` 留审批痕（custom 条目——转录卡片+持久化+恢复同形，
结构性不进 LLM 上下文）。
"""

from __future__ import annotations

from typing import Any

from nova_protocol import (
    ApprovalPolicy,
    ExecPolicyAmendment,
    ReviewDecision,
    ReviewDecisionPayload,
)

from nova_harness.core.agent_session.controllers.orchestrator import ApprovalAction
from nova_harness.core.harness.environments import RulesStore

from nova_coding_agent.ui_primitives import notify_message, select

_CHOICE_ONCE = "允许一次"
_CHOICE_SESSION = "本会话都允许"
_CHOICE_FOREVER = "永远允许（写入规则）"
_CHOICE_NO = "拒绝"


class ApprovalFlow:
    """审批流（扩展实例生命周期持有——会话缓存的闭包宿主）"""

    def __init__(self, rules_store: RulesStore, approval_policy: ApprovalPolicy) -> None:
        self._rules_store = rules_store
        self._approval_policy = approval_policy
        # 会话级缓存：(title, command) 精确签名——闭包状态 + 条目持久化恢复
        self._session_allowed: set[tuple[str, str | None]] = set()

    def restore_session_cache(self, keys: list[tuple[str, str | None]]) -> None:
        """会话条目恢复的缓存回填（session_start/session_tree 钩子里调）"""
        self._session_allowed = set(keys)

    def _record(
        self, ctx: Any, tool: str, target: str, decision: str, reason: str = ""
    ) -> None:
        """审批留痕（permission_gate 同款问记分离——dialog 管问，item 管记）"""
        append = getattr(ctx, "append_entry", None)
        if append is not None:
            append(
                "permission_decision",
                {"tool": tool, "target": target, "decision": decision, "reason": reason},
            )

    async def request_approval(
        self, action: ApprovalAction, ctx: Any
    ) -> ReviewDecisionPayload:
        tool_label = action.title
        target = action.command or ""

        # 1) fail-closed：never 档 / 无 UI
        if self._approval_policy is ApprovalPolicy.NEVER or not ctx.has_ui:
            self._record(ctx, tool_label, target, "blocked", "never 档或无 UI（fail-closed）")
            return ReviewDecisionPayload(
                decision=ReviewDecision.DENIED,
                rejection="审批不可用（approval_policy=never 或无 UI）",
            )

        # 2) 本会话缓存命中
        key = (tool_label, action.command)
        if key in self._session_allowed:
            return ReviewDecisionPayload(decision=ReviewDecision.APPROVED_FOR_SESSION)

        # 3) 弹窗
        text = f"⚠️ {action.title}"
        if action.command:
            text += f"\n\n  {action.command}"
        if action.reason:
            text += f"\n\n原因: {action.reason}"
        text += "\n\n允许执行？"
        choices = [_CHOICE_ONCE, _CHOICE_SESSION, _CHOICE_NO]
        if action.proposed_amendment_command is not None:
            choices.insert(2, _CHOICE_FOREVER)

        choice = await select(ctx.ui, text, choices)

        if choice == _CHOICE_ONCE:
            self._record(ctx, tool_label, target, "allow")
            return ReviewDecisionPayload(decision=ReviewDecision.APPROVED)

        if choice == _CHOICE_SESSION:
            self._session_allowed.add(key)
            self._record(ctx, tool_label, target, "always", "本会话内不再询问")
            return ReviewDecisionPayload(
                decision=ReviewDecision.APPROVED_FOR_SESSION
            )

        if choice == _CHOICE_FOREVER:
            amendment = ExecPolicyAmendment(
                command=tuple(action.proposed_amendment_command or ())
            )
            line = self._rules_store.append_amendment(amendment)
            self._record(ctx, tool_label, target, "always", f"规则写回: {line}")
            notify_message(ctx.ui, f"已写入规则: {line}")
            return ReviewDecisionPayload(
                decision=ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT,
                execpolicy_amendment=amendment,
            )

        self._record(ctx, tool_label, target, "deny", "用户拒绝")
        return ReviewDecisionPayload(
            decision=ReviewDecision.DENIED, rejection="用户拒绝"
        )
