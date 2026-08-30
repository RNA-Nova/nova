"""执行策略（SpawnPolicy）测试：档位解析、start_kwargs 透传、三态 cwd 规则。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from nova_coding_agent.executor import (
    BackendSelection,
    ExecutorBashOperations,
    SpawnPolicy,
    get_backend_selection,
    reset_backend_selection,
    resolve_spawn_policy,
    set_backend_selection,
)
from nova_coding_agent.executor.policy import SANDBOX_TIERS


class _Settings:
    """ExecutorSettings 形态的极简替身（只带档位与默认后端）。"""

    def __init__(
        self,
        sandbox: Optional[str] = None,
        default_backend: Optional[str] = None,
    ) -> None:
        self.sandbox = sandbox
        self.default_backend = default_backend


class _Ctx:
    """ExtensionContext 形态的极简替身（settings getter + cwd）。"""

    def __init__(self, settings: Any = None, cwd: str = "/tmp/proj") -> None:
        self._settings = settings
        self.cwd = cwd

    def get_executor_settings(self) -> Any:
        return self._settings


# ---------------------------------------------------------------------------
# SpawnPolicy.start_kwargs
# ---------------------------------------------------------------------------


def test_start_kwargs_empty_when_nothing_configured():
    assert SpawnPolicy().start_kwargs() == {}


def test_start_kwargs_carries_camel_wire_keys():
    sandbox = {"permissions": {"type": "managed"}}
    proxy = {"proxy": {"enabled": True}}
    policy = SpawnPolicy(
        sandbox=sandbox,
        network_proxy=proxy,
        enforce_managed_network=True,
        managed_network={"loopback": "allow"},
    )
    assert policy.start_kwargs() == {
        "sandbox": sandbox,
        "networkProxy": proxy,
        "enforceManagedNetwork": True,
        "managedNetwork": {"loopback": "allow"},
    }


def test_start_kwargs_skips_none_items():
    policy = SpawnPolicy(network_proxy={"proxy": {"enabled": True}})
    kwargs = policy.start_kwargs()
    assert "sandbox" not in kwargs
    assert "managedNetwork" not in kwargs
    assert "enforceManagedNetwork" not in kwargs


# ---------------------------------------------------------------------------
# resolve_spawn_policy：档位 → wire 形态
# ---------------------------------------------------------------------------


def test_resolve_returns_none_without_tier_or_cwd():
    assert resolve_spawn_policy(None, "/tmp/proj") is None
    assert resolve_spawn_policy(_Settings(sandbox="read-only"), None) is None
    assert resolve_spawn_policy(_Settings(sandbox=None), "/tmp/proj") is None


def test_resolve_read_only_wire_shape():
    policy = resolve_spawn_policy(_Settings(sandbox="read-only"), "/tmp/proj")
    assert policy is not None
    sandbox = policy.sandbox
    assert sandbox is not None
    assert sandbox["cwd"] == "/tmp/proj"
    permissions = sandbox["permissions"]
    assert permissions["type"] == "managed"
    assert permissions["network"] == "restricted"
    entries = permissions["fileSystem"]["entries"]
    assert entries[0]["access"] == "read"


def test_resolve_workspace_write_wire_shape():
    policy = resolve_spawn_policy(_Settings(sandbox="workspace-write"), "/tmp/proj")
    assert policy is not None
    sandbox = policy.sandbox
    assert sandbox is not None
    permissions = sandbox["permissions"]
    assert permissions["network"] == "enabled"
    entries = permissions["fileSystem"]["entries"]
    assert entries[0]["access"] == "write"


def test_sandbox_tiers_are_known():
    assert SANDBOX_TIERS == ("read-only", "workspace-write")


# ---------------------------------------------------------------------------
# BackendSelection 默认解析路径：档位随格生效
# ---------------------------------------------------------------------------


def setup_function(_):
    reset_backend_selection()


def teardown_function(_):
    reset_backend_selection()


def test_default_path_attaches_policy_for_executor_backend():
    settings = _Settings(default_backend="executor", sandbox="read-only")
    selection = get_backend_selection(settings)  # type: ignore[arg-type]
    assert selection.backend == "executor"
    assert selection.spawn_policy is not None


def test_default_path_local_backend_has_no_policy():
    settings = _Settings(default_backend="local", sandbox="read-only")
    selection = get_backend_selection(settings)  # type: ignore[arg-type]
    assert selection.spawn_policy is None


# ---------------------------------------------------------------------------
# ExecutorBashOperations：策略透传进 start_params
# ---------------------------------------------------------------------------


class _FakeProcHandle:
    def __init__(self) -> None:
        self.start_params: Dict[str, Any] = {}

    async def output(self):
        return
        yield  # pragma: no cover——空流的生成器形态

    async def read(self, wait_ms: int):
        class _Out:
            exit_code = 0

        return _Out()

    async def terminate(self):
        pass


class _FakeClient:
    def __init__(self, handle: _FakeProcHandle) -> None:
        self._handle = handle
        self.process = self

    async def start(self, **params):
        self._handle.start_params = params
        return self._handle


class _FakeManagerForOps:
    def __init__(self, client: _FakeClient) -> None:
        self._client = client

    async def get_client(self, url: Optional[str]):
        return self._client


def _run(ops: ExecutorBashOperations, cwd: str = "/tmp/proj"):
    return asyncio.run(ops.execute("echo hi", cwd, {}))


def test_operations_pass_policy_into_start_params():
    handle = _FakeProcHandle()
    policy = resolve_spawn_policy(_Settings(sandbox="workspace-write"), "/tmp/proj")
    assert policy is not None
    ops = ExecutorBashOperations(
        _FakeManagerForOps(_FakeClient(handle)),
        url=None,
        policy=policy,
        remote_cwd="/tmp/proj",
    )
    result = _run(ops)
    assert result.exit_code == 0
    assert handle.start_params["sandbox"] == policy.sandbox


def test_operations_without_policy_sends_no_sandbox():
    handle = _FakeProcHandle()
    ops = ExecutorBashOperations(_FakeManagerForOps(_FakeClient(handle)), url=None)
    result = _run(ops)
    assert result.exit_code == 0
    assert "sandbox" not in handle.start_params


# ---------------------------------------------------------------------------
# executor_switch._attach_policy：三态 cwd 规则
# ---------------------------------------------------------------------------


def _switch_module():
    """按扩展资源形态加载 executor_switch（extensions 不在 import 包内）。"""
    import importlib.util
    import os

    ext_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "extensions", "executor_switch.py"
    )
    spec = importlib.util.spec_from_file_location("_test_policy_ext", ext_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attach_policy_ssh_uses_remote_cwd():
    switch = _switch_module()
    ctx = _Ctx(_Settings(sandbox="read-only"))
    selection = BackendSelection(
        backend="executor", url="ssh://u@h", remote_cwd="/remote/w"
    )
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is not None
    assert selection.spawn_policy.sandbox is not None
    assert selection.spawn_policy.sandbox["cwd"] == "/remote/w"


def test_attach_policy_local_loopback_uses_local_cwd():
    switch = _switch_module()
    ctx = _Ctx(_Settings(sandbox="read-only"), cwd="/tmp/proj")
    selection = BackendSelection(backend="executor", url=None)
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is not None
    assert selection.spawn_policy.sandbox is not None
    assert selection.spawn_policy.sandbox["cwd"] == "/tmp/proj"


def test_attach_policy_ws_direct_without_remote_cwd_stays_unsandboxed():
    switch = _switch_module()
    ctx = _Ctx(_Settings(sandbox="read-only"), cwd="/tmp/proj")
    selection = BackendSelection(backend="executor", url="ws://host:28080")
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is None


def test_attach_policy_local_backend_never_sandboxes():
    switch = _switch_module()
    ctx = _Ctx(_Settings(sandbox="read-only"), cwd="/tmp/proj")
    selection = BackendSelection(backend="local")
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is None


# ---------------------------------------------------------------------------
# 防回归：切换后格上策略随 set_backend_selection 生效
# ---------------------------------------------------------------------------


def test_selection_roundtrip_keeps_policy():
    policy = SpawnPolicy(sandbox={"permissions": {}})
    set_backend_selection(
        BackendSelection(
            backend="executor",
            url="ssh://u@h",
            remote_cwd="/remote/w",
            spawn_policy=policy,
        )
    )
    assert get_backend_selection().spawn_policy is policy


def test_invalid_tier_resolves_to_no_policy():
    settings = _Settings(sandbox="yolo")
    assert settings.sandbox not in SANDBOX_TIERS
    assert resolve_spawn_policy(settings, "/tmp/proj") is None


@pytest.mark.parametrize("tier", ["read-only", "workspace-write"])
def test_both_tiers_produce_sandbox(tier: str):
    policy = resolve_spawn_policy(_Settings(sandbox=tier), "/tmp/proj")
    assert policy is not None
    assert policy.sandbox is not None
