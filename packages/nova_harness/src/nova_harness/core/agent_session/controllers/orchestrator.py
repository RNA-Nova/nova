"""ToolOrchestrator 对位物——执行类工具的编排漏斗（pull 库形态）。

逐点对位 codex `core/tools/orchestrator.rs` + `tools/sandboxing.rs`：

- **生命周期**：每次工具调用现造（codex 同款——实例只装本次调用的尝试
  状态；审批缓存/规则文件等跨调用状态全在调用方注入的回调里）。
- **审批要求自报**（`Approvable` 对位）：工具经
  `exec_approval_requirement(request)` 自报要求；返回 None 时按
  `default_exec_approval_requirement`（`sandboxing.rs:194` 移植）落默认。
- **编排顺序**（`orchestrator.rs:run` 对位）：裁决 → Forbidden 拒绝 /
  NeedsApproval 走审批流（`approval_action` + `request_approval` 回调）→
  attempt 执行（`runtime.run(request, attempt, ctx)`）→ 沙箱拒绝判别
  （nova_protocol 启发式）→ 放宽一档重试一次（审批已缓存不重问）。
- **遥测**：`otel.tool_decision` 对位——裁决/审批结局经 telemetry 回调
  上报（来源 tag：config/user/automated_reviewer）。

nova 简化（与 codex 差异登记）：

- 网络审批（`begin_network_approval`）不在本漏斗——nova 的网络裁决走
  exec-server 托管代理 + bundle network_gate 的独立通道（批次 C）。
- `SandboxAttempt` 收窄为：沙箱类型 + 展开的 FileSystemSandboxContext
  （套餐物化产物）+ 升级姿态——codex 的 PermissionProfile 引用群
  （permissions/exec_server_permissions/workspace_roots/linux_sandbox_exe/
  windows 旋钮）按 nova 的物化单点合并表达。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Protocol

from nova_protocol import (
    ApprovalPolicy,
    ExecApprovalRequirement,
    FileSystemSandboxContext,
    ReviewDecision,
    ReviewDecisionPayload,
    ToolDecisionSource,
    is_likely_sandbox_denied,
)

# =============================================================================
# 错误（对位 tools/sandboxing.rs ToolError）
# =============================================================================


class ToolRejected(Exception):
    """ToolError::Rejected——裁决禁止或审批拒绝"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# =============================================================================
# 尝试与输出
# =============================================================================


@dataclass(frozen=True)
class SandboxAttempt:
    """一次沙箱尝试（codex SandboxAttempt 的 nova 收窄形）。

    `sandbox_type`：本尝试的沙箱类型（None = 未沙箱，对位 SandboxType::None）；
    `sandbox_context`：展开的沙箱上下文（套餐物化产物，随 process/start 下发）。
    """

    sandbox_type: str | None = None
    sandbox_context: FileSystemSandboxContext | None = None


@dataclass(frozen=True)
class OrchestratorRequest:
    """一次编排的输入（冻结值对象）。

    `first_attempt` / `escalated_attempt`：首尝试与升级重试姿态
    （escalated_attempt=None 表示不可升级——被拒即终局）。
    """

    tool_name: str
    call_id: str
    params: dict = field(default_factory=dict)
    first_attempt: SandboxAttempt = field(default_factory=SandboxAttempt)
    escalated_attempt: SandboxAttempt | None = None

    def escalated(self) -> OrchestratorRequest:
        """升级姿态副本（重试一次用——升级后不再递归升级）"""
        if self.escalated_attempt is None:
            raise ValueError("no escalated attempt configured")
        return replace(
            self,
            first_attempt=self.escalated_attempt,
            escalated_attempt=None,
        )


@dataclass(frozen=True)
class ExecAttemptOutput:
    """一次尝试的输出（denial 判别的输入——与执行体约定形状）"""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    aggregated: str | None = None
    result: Any = None


# =============================================================================
# 审批动作（对位 ApprovalAction——弹窗内容描述）
# =============================================================================


@dataclass(frozen=True)
class ApprovalAction:
    """审批请求的内容描述（弹窗喂给前端的东西）"""

    title: str
    command: str | None = None
    reason: str | None = None
    proposed_amendment_command: tuple[str, ...] | None = None


# =============================================================================
# 工具协议（对位 Approvable + ToolRuntime）
# =============================================================================


class Approvable(Protocol):
    """审批自报协议（对位 tools/sandboxing.rs Approvable）"""

    def exec_approval_requirement(
        self, request: OrchestratorRequest
    ) -> ExecApprovalRequirement | None:
        """自报审批要求；None → 落 default_exec_approval_requirement 默认"""
        ...

    def should_bypass_approval(
        self, policy: ApprovalPolicy, already_approved: bool
    ) -> bool:
        """（对位 should_bypass_approval）已批准过/never 档 → 跳过"""
        ...

    def approval_action(self, request: OrchestratorRequest) -> ApprovalAction:
        """构造审批请求内容（对位 approval_action）"""
        ...


class ExecRuntime(Approvable, Protocol):
    """执行体协议（对位 ToolRuntime 的 nova 收窄形）"""

    async def run(
        self,
        request: OrchestratorRequest,
        attempt: SandboxAttempt,
        ctx: Any,
    ) -> ExecAttemptOutput:
        """真执行（唯一挂点）"""
        ...


# =============================================================================
# 默认审批要求（对位 sandboxing.rs:194 default_exec_approval_requirement）
# =============================================================================


def default_exec_approval_requirement(
    policy: ApprovalPolicy, *, fs_restricted: bool
) -> ExecApprovalRequirement:
    """默认审批要求（对位 codex 同名函数）。

    codex 映射（AskForApproval → 我方 ApprovalPolicy）：
    - Never → 不问（Skip）
    - OnRequest → 文件系统受限时问（Granular 的 auto-reject 变体 nova 未收）
    - UnlessTrusted → 总问；我方无此档
    - OnFailure（nova 档）→ 首尝试不问（Skip——失败后升级重试时再走审批，
      由调用方在升级路径单独处理）
    """
    needs_approval = policy in (ApprovalPolicy.ON_REQUEST,) and fs_restricted
    if needs_approval:
        return ExecApprovalRequirement.needs_approval()
    return ExecApprovalRequirement.skip()


# =============================================================================
# 回调签名
# =============================================================================

RequestApprovalFn = Callable[[ApprovalAction, Any], Awaitable[ReviewDecisionPayload]]
"""审批流回调（对位 session.request_approval）：动作描述 → 用户结局。
弹窗/会话缓存/amend 写回全归回调实现；headless 由调用方传 fail-closed deny。"""

TelemetryFn = Callable[[str, str, ToolDecisionSource, ReviewDecision], None]
"""决策遥测回调（对位 otel.tool_decision）：(tool_name, call_id, source, decision)"""


# =============================================================================
# 漏斗本体
# =============================================================================


class Orchestrator:
    """编排漏斗（codex ToolOrchestrator 对位物——每次工具调用现造）。

    构造注入：approval_policy（默认要求兜底档）、request_approval（审批流
    回调）、telemetry（可选遥测回调）。本类只持编排骨架与本次尝试状态。
    """

    def __init__(
        self,
        *,
        approval_policy: ApprovalPolicy,
        request_approval: RequestApprovalFn,
        telemetry: TelemetryFn | None = None,
    ) -> None:
        self._approval_policy = approval_policy
        self._request_approval = request_approval
        self._telemetry = telemetry or (
            lambda tool_name, call_id, source, decision: None
        )

    async def run(
        self, runtime: ExecRuntime, request: OrchestratorRequest, ctx: Any
    ) -> ExecAttemptOutput:
        # 1) 审批要求：工具自报优先，缺席落默认（对位 orchestrator.rs:171-183）
        requirement = runtime.exec_approval_requirement(request)
        if requirement is None:
            requirement = default_exec_approval_requirement(
                self._approval_policy,
                fs_restricted=request.first_attempt.sandbox_context is not None,
            )

        # 2) 裁决处理（对位 Skip/Forbidden/NeedsApproval 三分支）
        already_approved = False
        if requirement.kind == "forbidden":
            raise ToolRejected(requirement.reason or "execution forbidden")
        if requirement.kind == "needs_approval":
            action = runtime.approval_action(request)
            decision = await self._request_approval(action, ctx)
            self._telemetry(
                request.tool_name,
                request.call_id,
                ToolDecisionSource.USER,
                decision.decision,
            )
            if decision.decision not in (
                ReviewDecision.APPROVED,
                ReviewDecision.APPROVED_FOR_SESSION,
                ReviewDecision.APPROVED_EXECPOLICY_AMENDMENT,
                ReviewDecision.NETWORK_POLICY_AMENDMENT,
            ):
                raise ToolRejected(decision.rejection or "execution rejected by user")
            already_approved = True
        else:
            self._telemetry(
                request.tool_name,
                request.call_id,
                ToolDecisionSource.CONFIG,
                ReviewDecision.APPROVED,
            )

        # 3) 首尝试（对位 attempt → tool.run）
        output = await runtime.run(request, request.first_attempt, ctx)

        # 4) 沙箱拒绝判别 → 放宽一档重试一次（审批已缓存，不重问——
        #    对位 orchestrator.rs 的升级重试路径）
        if (
            request.escalated_attempt is not None
            and output.exit_code != 0
            and is_likely_sandbox_denied(
                request.first_attempt.sandbox_type,
                output.exit_code,
                output.stdout,
                output.stderr,
                output.aggregated,
            )
        ):
            return await runtime.run(
                request.escalated(), request.escalated_attempt, ctx
            )

        return output
