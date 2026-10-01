"""bash orchestrator 集成测试：沙箱尝试姿态 + 升级重试（denial 环激活）。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from nova_harness.core.agent_session.controllers.orchestrator import (
    ExecAttemptOutput,
    Orchestrator,
    OrchestratorRequest,
    SandboxAttempt,
)

from nova_coding_agent.executor import BackendSelection, SpawnPolicy
from nova_coding_agent.orchestration import AdjudicationAssembly
from tools.bash import _BashExecRuntime, _KEEP_POLICY


def _run(coro):
    return asyncio.run(coro)


class _FakeTool:
    """只提供 _run_engine 与 _context 的最小工具替身。"""

    def __init__(self, outputs):
        self._outputs = outputs
        self.calls: list = []
        self._context = SimpleNamespace()

    async def _run_engine(self, *args, **kwargs):
        self.calls.append(kwargs.get("spawn_policy", _KEEP_POLICY))
        return self._outputs[min(len(self.calls) - 1, len(self._outputs) - 1)]


def _fake_selection(sandboxed: bool):
    policy = (
        SpawnPolicy(sandbox={"cwd": "/tmp", "permissions": {}}) if sandboxed else None
    )
    return BackendSelection(backend="executor", url="ws://x", spawn_policy=policy)


def _patch_selection(sandboxed: bool):
    return patch(
        "tools.bash.get_backend_selection",
        lambda _settings=None: _fake_selection(sandboxed),
    )


def _orchestrator():
    async def approve(action, ctx):
        from nova_protocol import ReviewDecision, ReviewDecisionPayload

        return ReviewDecisionPayload(decision=ReviewDecision.APPROVED)

    from nova_protocol import ApprovalPolicy

    return Orchestrator(approval_policy=ApprovalPolicy.NEVER, request_approval=approve)


def test_runtime_sandboxes_detection():
    with _patch_selection(True):
        runtime = _BashExecRuntime(_FakeTool([]), "ls", "/tmp")
    assert runtime.sandboxes is True
    with _patch_selection(False):
        runtime = _BashExecRuntime(_FakeTool([]), "ls", "/tmp")
    assert runtime.sandboxes is False


def test_sandboxed_denial_escalates_to_unsandboxed_once():
    outputs = [SimpleNamespace(details={"exit_code": 0}, content=[], is_error=False)]
    tool = _FakeTool(outputs)
    with _patch_selection(True):
        runtime = _BashExecRuntime(tool, "touch /root/x", "/tmp")
        request = OrchestratorRequest(
            tool_name="bash",
            call_id="c1",
            first_attempt=SandboxAttempt(sandbox_type="executor_managed"),
            escalated_attempt=SandboxAttempt(sandbox_type=None),
        )
        # 直接调 runtime.run 模拟 orchestrator 的升级回调
        output = _run(runtime.run(request.escalated(), request.escalated_attempt, None))
    assert output.exit_code == 0
    # 升级尝试以脱沙箱姿态执行（spawn_policy=None 而非 _KEEP_POLICY）
    assert tool.calls == [None]


def test_unsandboxed_selection_no_escalation():
    tool = _FakeTool([SimpleNamespace(details={"exit_code": 0}, content=[])])
    with _patch_selection(False):
        runtime = _BashExecRuntime(tool, "ls", "/tmp")
        assert runtime.sandboxes is False
        request = OrchestratorRequest(
            tool_name="bash",
            call_id="c1",
            first_attempt=SandboxAttempt(sandbox_type=None),
            escalated_attempt=None,
        )
        output = _run(runtime.run(request, request.first_attempt, None))
    assert output.exit_code == 0
    assert tool.calls == [_KEEP_POLICY]


def test_full_orchestrator_denial_ring_with_sandbox():
    """端到端：沙箱拒绝 → orchestrator 判别 → 升级重试一次成功。"""
    outputs = [
        SimpleNamespace(
            details={"exit_code": 1, "stderr": "Operation not permitted"},
            content=[],
        ),
        SimpleNamespace(details={"exit_code": 0}, content=[]),
    ]
    tool = _FakeTool(outputs)
    with _patch_selection(True):
        runtime = _BashExecRuntime(tool, "touch /root/x", "/tmp")
        request = OrchestratorRequest(
            tool_name="bash",
            call_id="c1",
            params={"command": "touch /root/x"},
            first_attempt=SandboxAttempt(sandbox_type="executor_managed"),
            escalated_attempt=SandboxAttempt(sandbox_type=None),
        )
        orchestrator = _orchestrator()
        output = _run(orchestrator.run(runtime, request, None))
    assert output.exit_code == 0
    assert len(tool.calls) == 2
    # 首尝试带沙箱策略，升级尝试脱沙箱
    assert tool.calls[0] is _KEEP_POLICY
    assert tool.calls[1] is None


def test_orchestrated_path_passes_all_params_to_engine():
    """编排路径 params 直通（回归：OrchestratorRequest 曾只装 command/cwd，
    timeout/signal/on_update/spawn_hook/env 静默丢失——bash.py 的编排分支）。
    回退修复即红（stub 收到的 timeout/signal/on_update 全为 None）。"""
    from nova_harness.types.resources.tools import NULL_TOOL_SETTINGS, ToolContext
    from tools.bash import Tool

    import nova_coding_agent.orchestration as orch_mod

    recorded: dict = {}

    class _Result:
        content = []
        details = {"exit_code": 0}
        is_error = False

    tool = Tool(ToolContext(cwd="/tmp", settings=NULL_TOOL_SETTINGS))

    async def _stub_engine(
        command,
        cwd,
        env_extra,
        spawn_hook,
        timeout,
        signal,
        on_update,
        spawn_policy=_KEEP_POLICY,
    ):
        recorded.update(
            command=command,
            cwd=cwd,
            env=env_extra,
            spawn_hook=spawn_hook,
            timeout=timeout,
            signal=signal,
            on_update=on_update,
            spawn_policy=spawn_policy,
        )
        return _Result()

    tool._run_engine = _stub_engine  # type: ignore[method-assign]

    prev = orch_mod._assembly
    orch_mod.register_adjudication_assembly(
        AdjudicationAssembly(new_orchestrator=_orchestrator, engine=None)  # type: ignore[arg-type]
    )
    try:
        sentinel_signal = object()
        sentinel_hook = object()
        sentinel_update = lambda _text: None  # noqa: E731
        with _patch_selection(False):
            _run(
                tool.execute(
                    "c1",
                    {
                        "command": "echo hi",
                        "timeout": 30,
                        "env": {"FOO": "bar"},
                        "spawn_hook": sentinel_hook,
                    },
                    # signal/on_update 是 execute 的顶层参（不进 params dict）
                    signal=sentinel_signal,  # type: ignore[arg-type]
                    on_update=sentinel_update,
                )
            )
    finally:
        orch_mod._assembly = prev

    assert recorded["command"] == "echo hi"
    assert recorded["timeout"] == 30.0
    assert recorded["env"] == {"FOO": "bar"}
    assert recorded["signal"] is sentinel_signal
    assert recorded["spawn_hook"] is sentinel_hook
    assert recorded["on_update"] is sentinel_update
