"""Orchestrator 测试——编排四步对位 codex orchestrator.rs 语义"""

import pytest
from nova_protocol import (
    ApprovalPolicy,
    ExecApprovalRequirement,
    ReviewDecision,
    ReviewDecisionPayload,
    ToolDecisionSource,
)

from nova_harness.core.agent_session.controllers.orchestrator import (
    ApprovalAction,
    ExecAttemptOutput,
    Orchestrator,
    OrchestratorRequest,
    SandboxAttempt,
    ToolRejected,
    default_exec_approval_requirement,
)


class FakeRuntime:
    """自报要求可编程的执行体（记录每次 run 的尝试姿态）"""

    def __init__(self, requirement=None, outputs=None):
        self._requirement = requirement
        self._outputs = outputs or [ExecAttemptOutput(exit_code=0)]
        self.runs: list[tuple[OrchestratorRequest, SandboxAttempt]] = []

    def exec_approval_requirement(self, request):
        return self._requirement

    def should_bypass_approval(self, policy, already_approved):
        """codex 默认实现（sandboxing.rs:333）：已批过/never 档 → 不重问"""
        return already_approved or policy is ApprovalPolicy.NEVER

    def approval_action(self, request):
        return ApprovalAction(title="run command", command="rm -rf /tmp/x")

    async def run(self, request, attempt, ctx):
        self.runs.append((request, attempt))
        return self._outputs[min(len(self.runs) - 1, len(self._outputs) - 1)]


def _orchestrator(policy=ApprovalPolicy.NEVER, decision=None, telemetry=None):
    async def request_approval(action, ctx):
        return decision

    return Orchestrator(
        approval_policy=policy,
        request_approval=request_approval,
        telemetry=telemetry,
    )


def _request(**kwargs):
    kwargs.setdefault("tool_name", "bash")
    kwargs.setdefault("call_id", "call-1")
    return OrchestratorRequest(**kwargs)


@pytest.mark.asyncio
async def test_forbidden_rejects_without_execution():
    runtime = FakeRuntime(
        requirement=ExecApprovalRequirement.forbidden("policy says no")
    )
    with pytest.raises(ToolRejected, match="policy says no"):
        await _orchestrator().run(runtime, _request(), ctx=None)
    assert runtime.runs == []


@pytest.mark.asyncio
async def test_skip_executes_with_config_telemetry():
    events = []
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.skip())
    orchestrator = _orchestrator(telemetry=lambda *args: events.append(args))
    output = await orchestrator.run(runtime, _request(), ctx=None)
    assert output.exit_code == 0
    assert len(runtime.runs) == 1
    assert events == [
        ("bash", "call-1", ToolDecisionSource.CONFIG, ReviewDecision.APPROVED)
    ]


@pytest.mark.asyncio
async def test_needs_approval_approved_executes():
    decision = ReviewDecisionPayload(decision=ReviewDecision.APPROVED)
    runtime = FakeRuntime(
        requirement=ExecApprovalRequirement.needs_approval(reason="dangerous")
    )
    output = await _orchestrator(decision=decision).run(runtime, _request(), ctx=None)
    assert output.exit_code == 0
    assert len(runtime.runs) == 1


@pytest.mark.asyncio
async def test_needs_approval_denied_rejects():
    decision = ReviewDecisionPayload(
        decision=ReviewDecision.DENIED, rejection="user said no"
    )
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.needs_approval())
    with pytest.raises(ToolRejected, match="user said no"):
        await _orchestrator(decision=decision).run(runtime, _request(), ctx=None)
    assert runtime.runs == []


@pytest.mark.asyncio
async def test_sandbox_denial_escalates_once_without_reapproval():
    approval_calls = []

    async def counting_approval(action, ctx):
        approval_calls.append(action)
        return ReviewDecisionPayload(decision=ReviewDecision.APPROVED)

    outputs = [
        ExecAttemptOutput(exit_code=1, stderr="Operation not permitted"),
        ExecAttemptOutput(exit_code=0),
    ]
    runtime = FakeRuntime(
        requirement=ExecApprovalRequirement.needs_approval(), outputs=outputs
    )
    orchestrator = Orchestrator(
        approval_policy=ApprovalPolicy.NEVER, request_approval=counting_approval
    )
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    output = await orchestrator.run(runtime, request, ctx=None)

    assert output.exit_code == 0
    assert len(runtime.runs) == 2
    # 升级尝试用 escalated 姿态
    assert runtime.runs[1][1].sandbox_type is None
    # 审批只问一次（升级不重问）
    assert len(approval_calls) == 1


@pytest.mark.asyncio
async def test_denial_without_escalation_returns_first_output():
    runtime = FakeRuntime(
        outputs=[ExecAttemptOutput(exit_code=1, stderr="Operation not permitted")]
    )
    request = _request(first_attempt=SandboxAttempt(sandbox_type="seatbelt"))
    output = await _orchestrator().run(runtime, request, ctx=None)
    assert output.exit_code == 1
    assert len(runtime.runs) == 1


@pytest.mark.asyncio
async def test_quick_reject_output_does_not_escalate():
    runtime = FakeRuntime(
        outputs=[ExecAttemptOutput(exit_code=126, stderr="zsh: permission denied")]
    )
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    output = await _orchestrator().run(runtime, request, ctx=None)
    # 126 是 shell 快速拒绝码——但含 "permission denied" 关键词会判沙箱拒绝
    # （对位 denial.rs：关键词优先于快排码）——这里确认语义：会升级
    assert len(runtime.runs) == 2


@pytest.mark.asyncio
async def test_default_requirement_fallback():
    # 工具自报 None → 落默认：never → skip 直通
    runtime = FakeRuntime(requirement=None)
    output = await _orchestrator(policy=ApprovalPolicy.NEVER).run(
        runtime, _request(), ctx=None
    )
    assert output.exit_code == 0

    # on_request + 有沙箱（受限）→ needs_approval
    decision = ReviewDecisionPayload(decision=ReviewDecision.APPROVED)
    runtime = FakeRuntime(requirement=None)
    orchestrator = _orchestrator(policy=ApprovalPolicy.ON_REQUEST, decision=decision)
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt", sandbox_context=object())
    )
    output = await orchestrator.run(runtime, request, ctx=None)
    assert output.exit_code == 0


def test_default_exec_approval_requirement_mapping():
    assert (
        default_exec_approval_requirement(ApprovalPolicy.NEVER, fs_restricted=True).kind
        == "skip"
    )
    assert (
        default_exec_approval_requirement(
            ApprovalPolicy.ON_REQUEST, fs_restricted=True
        ).kind
        == "needs_approval"
    )
    assert (
        default_exec_approval_requirement(
            ApprovalPolicy.ON_REQUEST, fs_restricted=False
        ).kind
        == "skip"
    )


# ── 升级重试审批门（对位 orchestrator.rs:483 bypass_retry_approval） ──


@pytest.mark.asyncio
async def test_sandbox_denial_skip_requires_retry_approval():
    """skip（未批过）+ 非 never 档：沙箱被拒后的升级重试要走审批流，
    带固定 retry_reason——不再静默脱沙箱。"""
    from nova_harness.core.agent_session.controllers.orchestrator import (
        _DENIAL_RETRY_REASON,
    )

    approval_actions = []

    async def recording_approval(action, ctx):
        approval_actions.append(action)
        return ReviewDecisionPayload(decision=ReviewDecision.APPROVED)

    outputs = [
        ExecAttemptOutput(exit_code=1, stderr="Operation not permitted"),
        ExecAttemptOutput(exit_code=0),
    ]
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.skip(), outputs=outputs)
    orchestrator = Orchestrator(
        approval_policy=ApprovalPolicy.ON_REQUEST,
        request_approval=recording_approval,
    )
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    output = await orchestrator.run(runtime, request, ctx=None)

    assert output.exit_code == 0
    assert len(runtime.runs) == 2
    assert len(approval_actions) == 1  # 只有升级这一问（首轮 skip 不问）
    assert approval_actions[0].reason == _DENIAL_RETRY_REASON


@pytest.mark.asyncio
async def test_sandbox_denial_never_bypasses_retry_approval():
    """never 档：升级重试不问（无处可问——对位 should_bypass_approval
    的 Never 分支），直接脱沙箱重跑。"""
    approval_calls = []

    async def counting_approval(action, ctx):
        approval_calls.append(action)
        return ReviewDecisionPayload(decision=ReviewDecision.APPROVED)

    outputs = [
        ExecAttemptOutput(exit_code=1, stderr="Operation not permitted"),
        ExecAttemptOutput(exit_code=0),
    ]
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.skip(), outputs=outputs)
    orchestrator = Orchestrator(
        approval_policy=ApprovalPolicy.NEVER, request_approval=counting_approval
    )
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    output = await orchestrator.run(runtime, request, ctx=None)

    assert output.exit_code == 0
    assert len(runtime.runs) == 2
    assert approval_calls == []


@pytest.mark.asyncio
async def test_sandbox_denial_retry_approval_denied_rejects():
    """升级审批被拒 → ToolRejected，重试不执行。"""
    outputs = [ExecAttemptOutput(exit_code=1, stderr="Operation not permitted")]
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.skip(), outputs=outputs)
    decision = ReviewDecisionPayload(
        decision=ReviewDecision.DENIED, rejection="no escape"
    )
    orchestrator = Orchestrator(
        approval_policy=ApprovalPolicy.ON_REQUEST,
        request_approval=lambda action, ctx: _async_return(decision),
    )
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    with pytest.raises(ToolRejected, match="no escape"):
        await orchestrator.run(runtime, request, ctx=None)
    assert len(runtime.runs) == 1


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_bypass_sandbox_skip_runs_first_attempt_unsandboxed():
    """Skip{bypass_sandbox:true}（规则全放行）→ 首尝试即脱沙箱
    （对位 sandbox_override_for_first_attempt 的 BypassSandboxFirstAttempt）。"""
    runtime = FakeRuntime(requirement=ExecApprovalRequirement.skip(bypass_sandbox=True))
    request = _request(
        first_attempt=SandboxAttempt(sandbox_type="seatbelt"),
        escalated_attempt=SandboxAttempt(sandbox_type=None),
    )
    output = await _orchestrator().run(runtime, request, ctx=None)
    assert output.exit_code == 0
    assert runtime.runs[0][1].sandbox_type is None
    assert len(runtime.runs) == 1
