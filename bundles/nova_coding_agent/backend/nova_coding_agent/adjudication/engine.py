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
    ExecPolicyAmendment,
    intent_from_shell,
    is_dangerous_command,
)
from nova_protocol.exec_server_policy import (
    Evaluation,
    HeuristicsRuleMatch,
    Policy,
    PrefixRuleMatch,
)

# write/edit 保护路径片段（permission_gate 旧表收编——子串匹配）
_PROTECTED_PATH_PARTS = (".env", ".git/", "node_modules/")


def _dangerous_fallback(cmd: tuple[str, ...]) -> Decision:
    """危险启发式兜底（对位 codex 的 heuristics_fallback）：危险 → PROMPT"""
    return Decision.PROMPT if is_dangerous_command(cmd) else Decision.ALLOW


def _strictest(matched: list[PrefixRuleMatch | HeuristicsRuleMatch]) -> Decision:
    """命中集合的最严档（对位 Evaluation 的 max 聚合；空集 = allow）"""
    if not matched:
        return Decision.ALLOW
    return max(
        (m.decision for m in matched),
        key=lambda d: (Decision.ALLOW, Decision.PROMPT, Decision.FORBIDDEN).index(d),
    )


def _shell_gate_segment(
    shell: str | None, configured_shell: str | None
) -> tuple[str, ...] | None:
    """executable_identity 对位：自定义解释器进政策评估（上游
    exec_policy/executable_identity.rs 的 shell_approval_command）。

    豁免（不额外评估）：/bin、/usr/bin 系统位的解释器、与已配置 shell
    同路径者；其余（用户不认识的解释器二进制）作为独立段参与求值——
    内层命令只能加限制，不能给解释器授信。
    """
    if not shell:
        return None
    from pathlib import PurePath

    if PurePath(shell).parent.as_posix() in ("/bin", "/usr/bin"):
        return None
    if configured_shell and shell == configured_shell:
        return None
    return (shell,)


class AdjudicationEngine:
    """裁决引擎装配（每次规则 reload 现造；无跨调用状态）"""

    def __init__(self, policy: Policy, approval_policy: ApprovalPolicy) -> None:
        self._policy = policy
        self._approval_policy = approval_policy

    def adjudicate(
        self,
        tool_name: str,
        params: dict,
        ctx: Any,
        *,
        shell: str | None = None,
        configured_shell: str | None = None,
    ) -> ExecApprovalRequirement:
        if tool_name == "bash":
            return self._adjudicate_bash(
                params.get("command") or "",
                shell=shell,
                configured_shell=configured_shell,
            )
        if tool_name in ("write", "edit"):
            return self._adjudicate_write_path(params.get("path") or "")
        return ExecApprovalRequirement.skip()

    def _adjudicate_bash(
        self,
        command: str,
        *,
        shell: str | None = None,
        configured_shell: str | None = None,
    ) -> ExecApprovalRequirement:
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
        segments = list(intent.segments)
        matched: list[PrefixRuleMatch | HeuristicsRuleMatch] = []

        # 解释器身份门（上游 executable_identity）：自定义解释器单独求值——
        # 未见过的解释器永不自动放行（never 档除外：无处可问、信任沙箱），
        # 规则显式 allow 才免问；"永远允许"写回候选即解释器路径本身
        gate_segment = _shell_gate_segment(shell, configured_shell)
        if gate_segment is not None:
            shell_eval = self._policy.check(gate_segment, self._shell_fallback)
            matched.extend(shell_eval.matched_rules)
        cmd_eval = self._policy.check_multiple(segments, _dangerous_fallback)
        matched.extend(cmd_eval.matched_rules)

        evaluation = Evaluation(
            decision=_strictest(matched), matched_rules=tuple(matched)
        )

        if evaluation.decision is Decision.FORBIDDEN:
            return ExecApprovalRequirement.forbidden("规则禁止该命令")
        if evaluation.decision is Decision.PROMPT:
            gate_prompted = gate_segment is not None and any(
                isinstance(m, HeuristicsRuleMatch)
                and m.decision is Decision.PROMPT
                and m.command == gate_segment
                for m in matched
            )
            return ExecApprovalRequirement.needs_approval(
                reason=(f"使用未注册的解释器：{shell}" if gate_prompted else None),
                proposed_amendment=_derive_amendment_for_prompt(
                    evaluation.matched_rules
                ),
            )
        # ALLOW：全部段被规则显式 allow → 首尝试可跳过沙箱（对位 codex
        # Skip{bypass_sandbox}——启发式放行不算数）；另产出沙箱失败后的
        # 豁免写回候选（对位 try_derive_execpolicy_amendment_for_allow_rules）
        return ExecApprovalRequirement.skip(
            bypass_sandbox=self._all_segments_rule_allowed(segments),
            proposed_amendment=_derive_amendment_for_allow(evaluation.matched_rules),
        )

    def _shell_fallback(self, _cmd: tuple[str, ...]) -> Decision:
        """解释器段兜底：never 档放行（无处可问、信任沙箱）；其余一律 prompt
        ——未注册的解释器不能用启发式放行。"""
        if self._approval_policy is ApprovalPolicy.NEVER:
            return Decision.ALLOW
        return Decision.PROMPT

    def _all_segments_rule_allowed(self, segments: list[tuple[str, ...]]) -> bool:
        """每段都被规则显式 allow（无兜底参与的纯规则求值——对位上游
        Skip.bypass_sandbox 的逐段 matches_for_command(None 兜底) 判定）。"""
        for segment in segments:
            evaluation = self._policy.check(segment)
            if not any(
                isinstance(m, PrefixRuleMatch) and m.decision is Decision.ALLOW
                for m in evaluation.matched_rules
            ):
                return False
        return True

    def _adjudicate_write_path(self, path: str) -> ExecApprovalRequirement:
        hit = next((part for part in _PROTECTED_PATH_PARTS if part in path), None)
        if hit is None:
            return ExecApprovalRequirement.skip()
        return ExecApprovalRequirement.forbidden(f'路径 "{path}" 受保护（含 "{hit}"）')


def _derive_amendment_for_prompt(
    matched_rules: tuple[PrefixRuleMatch | HeuristicsRuleMatch, ...],
) -> ExecPolicyAmendment | None:
    """prompt 结局的写回候选：有规则 prompt → None（写回也绕不过规则）；
    否则首个启发式 prompt 段的命令词（对位 try_derive_execpolicy_amendment_
    for_prompt_rules）。"""
    if any(
        isinstance(m, PrefixRuleMatch) and m.decision is Decision.PROMPT
        for m in matched_rules
    ):
        return None
    for m in matched_rules:
        if isinstance(m, HeuristicsRuleMatch) and m.decision is Decision.PROMPT:
            if m.command:
                return ExecPolicyAmendment(command=m.command)
    return None


def _derive_amendment_for_allow(
    matched_rules: tuple[PrefixRuleMatch | HeuristicsRuleMatch, ...],
) -> ExecPolicyAmendment | None:
    """allow 结局的沙箱豁免候选：有规则命中 → None（本就不会被沙箱拦的
    语义不成立——对位 try_derive_execpolicy_amendment_for_allow_rules：
    规则已命中说明命令在规则面已放行，无需再写）；否则首个启发式 allow 段。"""
    if any(isinstance(m, PrefixRuleMatch) for m in matched_rules):
        return None
    for m in matched_rules:
        if isinstance(m, HeuristicsRuleMatch) and m.decision is Decision.ALLOW:
            if m.command:
                return ExecPolicyAmendment(command=m.command)
    return None
