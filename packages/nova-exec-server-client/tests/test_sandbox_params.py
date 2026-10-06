"""沙箱上下文与进程启动参数的 wire 形态测试（序列化正确性，无需传输层）"""

import pytest
from nova_protocol import (
    ExecFileSystemPath,
    ExecPermissionProfile,
    FileSystemAccessMode,
    FileSystemSandboxContext,
    NetworkSandboxPolicy,
    ProcessStartParams,
    WindowsSandboxLevel,
)
from pydantic import ValidationError


def test_read_only_sandbox_serializes_wire_shape():
    """codex `:read-only` 套餐 wire 形态：全盘可读（符号 :root）、无处可写、
    网络受限；v1.10 起策略目录由 policyContext 承载（只读基座无需
    cwd 的策略不再平铺 legacy cwd——对位 codex 841b5490b2）"""
    ctx = FileSystemSandboxContext.read_only("/tmp/proj")
    # exclude_none 对齐 process/start 的真实出货路径（None 项不上线）
    data = ctx.model_dump(by_alias=True, exclude_none=True)
    assert data["permissions"]["type"] == "managed"
    assert data["permissions"]["fileSystem"]["type"] == "restricted"
    entries = data["permissions"]["fileSystem"]["entries"]
    assert entries == [
        {"path": {"type": "special", "value": {"kind": "root"}}, "access": "read"}
    ]
    assert data["permissions"]["network"] == "restricted"
    assert "cwd" not in data  # 策略不依赖 cwd 时 legacy 平铺字段省略
    assert data["policyContext"] == {"cwd": "/tmp/proj", "workspaceRoots": []}
    assert data["windowsSandboxLevel"] == "disabled"
    assert data["useLegacyLandlock"] is False


def test_cwd_dependent_sandbox_keeps_legacy_flat_fields():
    """策略含 project_roots 符号（或相对 glob）时，legacy 平铺 cwd/workspaceRoots
    照旧上线（老 executor 兼容），policyContext 同时承载（对位 codex 841b5490b2）"""
    ctx = FileSystemSandboxContext.workspace_write("/tmp/proj")
    ctx.workspace_roots = ["/tmp/proj"]
    data = ctx.model_dump(by_alias=True, exclude_none=True)
    assert data["cwd"] == "/tmp/proj"
    assert data["workspaceRoots"] == ["/tmp/proj"]
    assert data["policyContext"] == {
        "cwd": "/tmp/proj",
        "workspaceRoots": ["/tmp/proj"],
    }

    # 老客户端发来的平铺形态（无 policyContext）照旧可解析
    legacy = FileSystemSandboxContext.model_validate(
        {"cwd": "/tmp/proj", "workspaceRoots": ["/tmp/proj"]}
    )
    assert legacy.policy_context is None
    assert legacy.cwd == "/tmp/proj"
    assert legacy.workspace_roots == ["/tmp/proj"]


def test_legacy_flat_fields_follow_glob_convention():
    """绝对 glob（按 cwd 约定）省略 legacy 平铺；相对 glob 保留——对位 codex
    841b5490b2 的 remote_file_system_preserves_only_cwd_dependent_policy_directories"""
    from nova_protocol.exec_server_wire import (
        ExecFileSystemPath,
        ExecFileSystemSandboxEntry,
        ExecManagedFileSystemPermissions,
        ExecPermissionProfile,
        FileSystemAccessMode,
    )

    def ctx_with_glob(cwd: str, pattern: str) -> FileSystemSandboxContext:
        return FileSystemSandboxContext(
            cwd=cwd,
            permissions=ExecPermissionProfile(
                file_system=ExecManagedFileSystemPermissions(
                    entries=[
                        ExecFileSystemSandboxEntry(
                            path=ExecFileSystemPath.glob(pattern),
                            access=FileSystemAccessMode.DENY,
                        )
                    ]
                )
            ),
        )

    for cwd, pattern, expect_flat in [
        ("file:///workspace/checkout", "/elsewhere/*.secret", False),
        ("file:///D:/checkout", r"D:\elsewhere\*.secret", False),
        ("file://server/share/checkout", r"\\server\share\elsewhere\*.secret", False),
        ("file:///workspace/checkout", "*.secret", True),
        ("file:///D:/checkout", r"private\*.secret", True),
    ]:
        data = ctx_with_glob(cwd, pattern).model_dump(by_alias=True, exclude_none=True)
        assert ("cwd" in data) is expect_flat, (cwd, pattern)
        assert data["policyContext"]["cwd"] == cwd


def test_workspace_write_sandbox_serializes_roots_and_network():
    """codex `:workspace` 套餐 wire 形态：只读基座 + 项目根写 + 临时目录写 +
    用户附加根写 + 元数据降只读；网络默认受限"""
    ctx = FileSystemSandboxContext.workspace_write("/tmp/proj", ["/tmp/extra"])
    profile = ctx.model_dump(by_alias=True, exclude_none=True)["permissions"]
    assert profile["network"] == "restricted"
    entries = profile["fileSystem"]["entries"]
    # _file_url 不 resolve（对位 rust from_host_native_path 只查绝对性），按字面路径算期望
    extra_uri = "file:///tmp/extra"
    assert [(e["path"], e["access"]) for e in entries] == [
        ({"type": "special", "value": {"kind": "root"}}, "read"),
        ({"type": "special", "value": {"kind": "project_roots"}}, "write"),
        ({"type": "special", "value": {"kind": "slash_tmp"}}, "write"),
        ({"type": "special", "value": {"kind": "tmpdir"}}, "write"),
        ({"type": "path", "path": extra_uri}, "write"),
        (
            {"type": "special", "value": {"kind": "project_roots", "subpath": ".git"}},
            "read",
        ),
        (
            {"type": "special", "value": {"kind": "project_roots", "subpath": ".nova"}},
            "read",
        ),
    ]


def test_workspace_write_network_enabled_opt_in():
    """网络放行是显式参数（档位默认受限——放行归 network_proxy 名单）"""
    ctx = FileSystemSandboxContext.workspace_write(
        "/tmp/proj", network=NetworkSandboxPolicy.ENABLED
    )
    assert ctx.permissions.network == NetworkSandboxPolicy.ENABLED
    assert (
        FileSystemSandboxContext.workspace_write("/tmp/proj").permissions.network
        == NetworkSandboxPolicy.RESTRICTED
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
    profile = ExecPermissionProfile(
        type="external", network=NetworkSandboxPolicy.ENABLED
    )
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
