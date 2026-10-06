"""执行策略（SpawnPolicy）测试：档位解析、start_kwargs 透传、三态 cwd 规则。"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import pytest
from nova_exec_server_client import load_executor_config

from nova_coding_agent.executor import (
    BackendSelection,
    ExecutorBashOperations,
    SpawnPolicy,
    get_backend_selection,
    reset_backend_selection,
    set_backend_selection,
)


class _Ctx:
    """ExtensionContext 形态的极简替身（只带 cwd）。"""

    def __init__(self, cwd: str = "/tmp/proj") -> None:
        self.cwd = cwd


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


def _config_with_mode(tmp_path, mode: str | None):
    """临时 exec-server home 写 config.toml（物化输入注入）。"""
    home = tmp_path / "home"
    home.mkdir()
    text = f'sandbox_mode = "{mode}"\n' if mode else ""
    (home / "config.toml").write_text(text)
    return home


def test_resolve_returns_none_without_mode_or_cwd(tmp_path):
    from nova_protocol import ExecutorConfig

    config = ExecutorConfig()
    assert config.resolve_execution("/tmp/proj").sandbox is None
    config = ExecutorConfig(
        sandbox_mode=ExecutorConfig(sandbox_mode="read-only").sandbox_mode
    )
    assert config.resolve_execution(None).sandbox is None


def test_resolve_read_only_wire_shape(tmp_path):
    home = _config_with_mode(tmp_path, "read-only")
    config = load_executor_config(executor_home=home)
    sandbox = config.to_file_system_sandbox("/tmp/proj")
    assert sandbox is not None
    payload = sandbox.model_dump(by_alias=True, exclude_none=True)
    # v1.11 终态：策略目录只经 policyContext 承载（平铺字段已删）
    assert "cwd" not in payload
    assert payload["policyContext"]["cwd"] == "/tmp/proj"
    permissions = payload["permissions"]
    assert permissions["type"] == "managed"
    assert permissions["network"] == "restricted"
    entries = permissions["fileSystem"]["entries"]
    assert entries[0]["access"] == "read"


def test_resolve_workspace_write_wire_shape(tmp_path):
    """workspace-write 套餐 → codex :workspace 展开（网络默认受限，基座+项目根）"""
    home = _config_with_mode(tmp_path, "workspace-write")
    config = load_executor_config(executor_home=home)
    payload = config.to_file_system_sandbox("/tmp/proj").model_dump(
        by_alias=True, exclude_none=True
    )
    permissions = payload["permissions"]
    assert permissions["network"] == "restricted"
    entries = permissions["fileSystem"]["entries"]
    # codex 形态：第 1 条是全盘只读基座（符号 :root），第 2 条项目根可写
    assert entries[0]["access"] == "read"
    assert entries[0]["path"]["value"]["kind"] == "root"
    assert entries[1]["access"] == "write"
    assert entries[1]["path"]["value"]["kind"] == "project_roots"


# ---------------------------------------------------------------------------
# BackendSelection 默认解析路径：档位随格生效
# ---------------------------------------------------------------------------


def setup_function(_):
    reset_backend_selection()


def teardown_function(_):
    reset_backend_selection()


def test_default_path_attaches_policy_when_sandbox_mode_configured(
    monkeypatch, tmp_path
):
    """默认执行姿态归 config.toml：配了 sandbox_mode → 本地回环 executor 带沙箱。"""
    home = _config_with_mode(tmp_path, "read-only")
    import nova_coding_agent.executor.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod,
        "load_executor_config",
        lambda **_: load_executor_config(executor_home=home),
    )
    selection = get_backend_selection()
    assert selection.backend == "executor"
    assert selection.url is None
    assert selection.spawn_policy is not None


def test_default_path_local_backend_without_sandbox_mode(monkeypatch, tmp_path):
    """未配 sandbox_mode → 本地直接执行，无策略。"""
    home = _config_with_mode(tmp_path, None)
    import nova_coding_agent.executor.runtime as runtime_mod

    monkeypatch.setattr(
        runtime_mod,
        "load_executor_config",
        lambda **_: load_executor_config(executor_home=home),
    )
    selection = get_backend_selection()
    assert selection.backend == "local"
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


def test_operations_pass_policy_into_start_params(tmp_path):
    handle = _FakeProcHandle()
    home = _config_with_mode(tmp_path, "workspace-write")
    policy = SpawnPolicy(
        sandbox=load_executor_config(executor_home=home)
        .to_file_system_sandbox("/tmp/proj")
        .model_dump(by_alias=True, exclude_none=True)
    )
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


def test_attach_policy_ssh_uses_remote_cwd(monkeypatch, tmp_path):
    home = _config_with_mode(tmp_path, "read-only")
    switch = _switch_module()
    monkeypatch.setattr(
        switch,
        "load_executor_config",
        lambda **_: load_executor_config(executor_home=home),
    )
    ctx = _Ctx()
    selection = BackendSelection(
        backend="executor", url="ssh://u@h", remote_cwd="/remote/w"
    )
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is not None
    assert selection.spawn_policy.sandbox is not None
    # v1.11 终态：策略目录只经 policyContext 承载
    assert selection.spawn_policy.sandbox["policyContext"]["cwd"] == "/remote/w"


def test_attach_policy_local_loopback_uses_local_cwd(monkeypatch, tmp_path):
    home = _config_with_mode(tmp_path, "read-only")
    switch = _switch_module()
    monkeypatch.setattr(
        switch,
        "load_executor_config",
        lambda **_: load_executor_config(executor_home=home),
    )
    ctx = _Ctx(cwd="/tmp/proj")
    selection = BackendSelection(backend="executor", url=None)
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is not None
    assert selection.spawn_policy.sandbox is not None
    # v1.11 终态：策略目录只经 policyContext 承载
    assert selection.spawn_policy.sandbox["policyContext"]["cwd"] == "/tmp/proj"


def test_attach_policy_ws_direct_without_remote_cwd_stays_unsandboxed(
    monkeypatch, tmp_path
):
    home = _config_with_mode(tmp_path, "read-only")
    switch = _switch_module()
    monkeypatch.setattr(
        switch,
        "load_executor_config",
        lambda **_: load_executor_config(executor_home=home),
    )
    ctx = _Ctx(cwd="/tmp/proj")
    selection = BackendSelection(backend="executor", url="ws://host:28080")
    switch._attach_policy(ctx, selection)
    assert selection.spawn_policy is None


def test_attach_policy_local_backend_never_sandboxes():
    switch = _switch_module()
    ctx = _Ctx(cwd="/tmp/proj")
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


def test_invalid_mode_is_config_error(tmp_path):
    import tomllib

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text('sandbox_mode = "yolo"\n')
    with pytest.raises(Exception):
        load_executor_config(executor_home=home)


@pytest.mark.parametrize("tier", ["read-only", "workspace-write"])
def test_both_tiers_produce_sandbox(tier: str, tmp_path):
    home = _config_with_mode(tmp_path, tier)
    policy = SpawnPolicy(
        sandbox=load_executor_config(executor_home=home)
        .to_file_system_sandbox("/tmp/proj")
        .model_dump(by_alias=True, exclude_none=True)
    )
    assert policy is not None
    assert policy.sandbox is not None


def test_operations_shell_override_goes_to_argv():
    """单次调用级解释器覆盖（options.shell）→ argv[0]；缺省回默认 bash。"""
    handle = _FakeProcHandle()
    ops = ExecutorBashOperations(_FakeManagerForOps(_FakeClient(handle)), url=None)
    result = asyncio.run(
        ops.execute("echo hi", "/tmp/proj", {"shell": "/opt/custom-sh"})
    )
    assert result.exit_code == 0
    assert handle.start_params["argv"][0] == "/opt/custom-sh"

    handle2 = _FakeProcHandle()
    ops2 = ExecutorBashOperations(_FakeManagerForOps(_FakeClient(handle2)), url=None)
    asyncio.run(ops2.execute("echo hi", "/tmp/proj", {}))
    assert handle2.start_params["argv"][0] == "bash"
