"""政策装配：intent 分析 + execpolicy 求值 → `adjudicate` 回调。

分层纪律：引擎在 nova_protocol（execpolicy/intent），规则 I/O 在 harness
（RulesStore），本模块只做"三件接起来"——读一次规则、对每次调用做
①分析 ②求值 ③映射成 ExecApprovalRequirement。

映射（对位 codex exec_policy.rs 的结论形状）：

- Decision.FORBIDDEN → forbidden（规则禁止）
- Decision.PROMPT → needs_approval（弹窗——含危险启发式兜底命中）
- Decision.ALLOW → skip
- 不可解析（opaque）→ needs_approval（压力上移询问层，不悲观降级——
  对位 codex fail-open 边界：不可解析≠危险）
"""

from __future__ import annotations

from typing import Any

from nova_protocol import (
    ApprovalPolicy,
    Decision,
    ExecApprovalRequirement,
    intent_from_shell,
    is_dangerous_command,
)
from nova_protocol.exec_server_policy import Policy

# write/edit 保护路径片段（permission_gate 旧表收编——子串匹配）
_PROTECTED_PATH_PARTS = (".env", ".git/", "node_modules/")


def _dangerous_fallback(cmd: tuple[str, ...]) -> Decision:
    """危险启发式兜底（对位 codex 的 heuristics_fallback）：危险 → PROMPT"""
    return Decision.PROMPT if is_dangerous_command(cmd) else Decision.ALLOW


class AdjudicationEngine:
    """裁决引擎装配（每次规则 reload 现造；无跨调用状态）"""

    def __init__(self, policy: Policy, approval_policy: ApprovalPolicy) -> None:
        self._policy = policy
        self._approval_policy = approval_policy

    def adjudicate(self, tool_name: str, params: dict, ctx: Any) -> ExecApprovalRequirement:
        if tool_name == "bash":
            return self._adjudicate_bash(params.get("command") or "")
        if tool_name in ("write", "edit"):
            return self._adjudicate_write_path(params.get("path") or "")
        return ExecApprovalRequirement.skip()

    def _adjudicate_bash(self, command: str) -> ExecApprovalRequirement:
        if not command:
            return ExecApprovalRequirement.skip()

        # ① 分析（nova_protocol intent——复合拆分）
        intent = intent_from_shell(command)
        if intent.opaque:
            return ExecApprovalRequirement.needs_approval(
                reason="命令含动态展开，无法静态判定"
            )

        # ② execpolicy 求值（逐段 + 危险启发式兜底——对位 codex 的
        # check_multiple(commands, dangerous_fallback)）
        evaluation = self._policy.check_multiple(
            list(intent.segments), _dangerous_fallback
        )

        if evaluation.decision is Decision.FORBIDDEN:
            return ExecApprovalRequirement.forbidden("规则禁止该命令")
        if evaluation.decision is Decision.PROMPT:
            return ExecApprovalRequirement.needs_approval()
        return ExecApprovalRequirement.skip()

    def _adjudicate_write_path(self, path: str) -> ExecApprovalRequirement:
        hit = next(
            (part for part in _PROTECTED_PATH_PARTS if part in path), None
        )
        if hit is None:
            return ExecApprovalRequirement.skip()
        return ExecApprovalRequirement.forbidden(f'路径 "{path}" 受保护（含 "{hit}"）')
