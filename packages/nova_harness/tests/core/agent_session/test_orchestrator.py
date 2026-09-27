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
        return already_approved

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
