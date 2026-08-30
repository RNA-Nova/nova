"""沙箱上下文与进程启动参数的 wire 形态测试（序列化正确性，无需传输层）"""

import pytest
from pydantic import ValidationError

from nova_executor_client.protocol import (
    ExecFileSystemPath,
    ExecPermissionProfile,
    FileSystemAccessMode,
    FileSystemSandboxContext,
    NetworkSandboxPolicy,
    ProcessStartParams,
    WindowsSandboxLevel,
)


def test_read_only_sandbox_serializes_wire_shape():
    ctx = FileSystemSandboxContext.read_only("/tmp/proj")
    data = ctx.model_dump(by_alias=True)
    assert data["permissions"]["type"] == "managed"
    assert data["permissions"]["fileSystem"]["type"] == "restricted"
    entries = data["permissions"]["fileSystem"]["entries"]
    assert entries[0]["access"] == "read"
    assert entries[0]["path"]["type"] == "path"
    assert entries[0]["path"]["path"].startswith("file:///")
    assert data["windowsSandboxLevel"] == "disabled"
    assert data["useLegacyLandlock"] is False


def test_workspace_write_sandbox_serializes_roots_and_network():
    ctx = FileSystemSandboxContext.workspace_write(
        "/tmp/proj", ["/tmp/extra"], network_enabled=False
    )
    profile = ctx.model_dump(by_alias=True)["permissions"]
    assert profile["network"] == "restricted"
    entries = profile["fileSystem"]["entries"]
    accesses = [e["access"] for e in entries]
    assert accesses == ["write", "write"]


def test_workspace_write_network_enabled():
    ctx = FileSystemSandboxContext.workspace_write("/tmp/proj")
    assert (
        ctx.permissions.network == NetworkSandboxPolicy.ENABLED
    )


def test_start_params_sandbox_passes_through_camel_case():
    ctx = FileSystemSandboxContext.read_only("/tmp/proj")
    params = ProcessStartParams(
        processId="p1",
        argv=["bash", "-c", "true"],
        cwd="/tmp/proj",
        env={},
        sandbox=ctx.model_dump(by_alias=True),
        enforceManagedNetwork=True,
        managedNetwork={"loopbackPorts": [8080], "allowLocalBinding": True},
        arg0="bash",
    )
    wire = params.model_dump(by_alias=True, exclude_none=True)
    assert wire["sandbox"]["permissions"]["type"] == "managed"
    assert wire["enforceManagedNetwork"] is True
    assert wire["managedNetwork"]["loopbackPorts"] == [8080]
    assert wire["arg0"] == "bash"
    # 未设置的Optional字段经 exclude_none 剔除
    assert "shellSnapshot" not in wire
    assert "envPolicy" not in wire


def test_windows_sandbox_level_kebab_case():
    assert WindowsSandboxLevel.RESTRICTED_TOKEN == "restricted-token"


def test_exec_permission_profile_external_variant():
    profile = ExecPermissionProfile(type="external", network=NetworkSandboxPolicy.ENABLED)
    wire = profile.model_dump(by_alias=True)
    # external 变体带默认 file_system 字段（pydantic 含默认值字段照常序列化）
    assert wire == {
        "type": "external",
        "fileSystem": {"type": "restricted", "entries": [], "globScanMaxDepth": None},
        "network": "enabled",
    }


def test_invalid_profile_type_rejected():
    with pytest.raises(ValidationError):
        ExecFileSystemPath(type="bogus")
    with pytest.raises(ValidationError):
        ExecPermissionProfile(type="bogus")
