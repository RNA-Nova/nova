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
    网络受限；v1.11 起策略目录只经 policyContext 承载（终态形状——平铺
    cwd/workspaceRoots 字段已删，对位 codex 841b5490b2 的终态化）"""
    ctx = FileSystemSandboxContext.read_only("/tmp/proj")
    # exclude_none 对齐 process/start 的真实出货路径（None 项不上线）
    data = ctx.model_dump(by_alias=True, exclude_none=True)
    assert data["permissions"]["type"] == "managed"
    assert data["permissions"]["file_system"]["type"] == "restricted"
    entries = data["permissions"]["file_system"]["entries"]
    assert entries == [
        {"path": {"type": "special", "value": {"kind": "root"}}, "access": "read"}
    ]
    assert data["permissions"]["network"] == "restricted"
    assert "cwd" not in data
    assert "workspaceRoots" not in data
    assert data["policyContext"] == {
        "cwd": "/tmp/proj",
        "workspaceRoots": ["/tmp/proj"],
    }
    assert data["windowsSandboxLevel"] == "disabled"
    assert data["useLegacyLandlock"] is False


def test_legacy_flat_policy_fields_silently_ignored():
    """纯 legacy 平铺载荷（v1.11 前的平铺 cwd/workspaceRoots 双形状字段）被
    静默忽略并回退：model_validate 不炸、policy_context 落 None（= 客户端
    整个省略时 executor 入口回退自身 cwd 的人机工学路径），dump 回线上也
    不再出现平铺键（v1.11 终态单形状钉住）"""
    legacy = FileSystemSandboxContext.model_validate(
        {"cwd": "/tmp/proj", "workspaceRoots": ["/tmp/proj"]}
    )
    assert legacy.policy_context is None
    data = legacy.model_dump(by_alias=True, exclude_none=True)
    assert "cwd" not in data
    assert "workspaceRoots" not in data
    assert "policyContext" not in data


def test_policy_context_is_the_only_directory_carrier():
    """任意策略形态（含 project_roots 符号/相对 glob）线上都只有
    policyContext 一种承载；平铺键永不出现（v1.11 终态，对位 codex
    841b5490b2 的终态化）"""
    from nova_protocol.exec_server_wire import (
        ExecFileSystemPath,
        ExecFileSystemSandboxEntry,
        ExecManagedFileSystemPermissions,
        ExecPermissionProfile,
        FileSystemAccessMode,
        WireFileSystemPolicyContext,
    )

    for cwd in [
        "/tmp/proj",
        "file:///workspace/checkout",
        "file:///D:/checkout",
        "file://server/share/checkout",
    ]:
        ctx = FileSystemSandboxContext(
            policy_context=WireFileSystemPolicyContext(cwd=cwd, workspaceRoots=[cwd]),
            permissions=ExecPermissionProfile(
                file_system=ExecManagedFileSystemPermissions(
                    entries=[
                        ExecFileSystemSandboxEntry(
                            path=ExecFileSystemPath.glob("*.secret"),
                            access=FileSystemAccessMode.DENY,
                        ),
                        ExecFileSystemSandboxEntry(
                            path=ExecFileSystemPath.project_roots(),
                            access=FileSystemAccessMode.WRITE,
                        ),
                    ]
                )
            ),
        )
        data = ctx.model_dump(by_alias=True, exclude_none=True)
        assert "cwd" not in data and "workspaceRoots" not in data
        assert data["policyContext"] == {"cwd": cwd, "workspaceRoots": [cwd]}

    # 省略 policyContext = 客户端让 executor 入口回退自身 cwd（人机工学，保留）
    ctx = FileSystemSandboxContext.read_only()
    data = ctx.model_dump(by_alias=True, exclude_none=True)
    assert "policyContext" not in data


def test_workspace_write_sandbox_serializes_roots_and_network():
    """codex `:workspace` 套餐 wire 形态：只读基座 + 项目根写 + 临时目录写 +
    用户附加根写 + 元数据降只读；网络默认受限"""
    ctx = FileSystemSandboxContext.workspace_write("/tmp/proj", ["/tmp/extra"])
    profile = ctx.model_dump(by_alias=True, exclude_none=True)["permissions"]
    assert profile["network"] == "restricted"
    entries = profile["file_system"]["entries"]
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
        "file_system": {"type": "restricted", "entries": [], "glob_scan_max_depth": None},
        "network": "enabled",
    }


def test_invalid_profile_type_rejected():
    with pytest.raises(ValidationError):
        ExecFileSystemPath(type="bogus")
    with pytest.raises(ValidationError):
        ExecPermissionProfile(type="bogus")
