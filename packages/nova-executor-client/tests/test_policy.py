"""物化层测试（policy.py）

覆盖：套餐展开对位 codex 语义、workspace-write 微调旋钮、网络代理物化、
ask 行为降级（fail-closed）、wire 形状 pin（camelCase 对位 rust serde）。
"""

from nova_executor_client import (
    ApprovalPolicy,
    ExecutorConfig,
    NetworkProxySettings,
    SandboxMode,
    SandboxWorkspaceWriteConfig,
    resolve_ask_behavior,
    resolve_execution_policy,
    resolve_file_system_sandbox,
    resolve_network_proxy,
)
from nova_executor_client.protocol import (
    ExecFileSystemPath,
    FileSystemAccessMode,
    NetworkMode,
    NetworkSandboxPolicy,
)

CWD = "/tmp/workspace"


def _entries(ctx):
    return ctx.permissions.file_system.entries


class TestFileSystemSandbox:
    def test_absent_mode_returns_none(self):
        assert resolve_file_system_sandbox(ExecutorConfig(), CWD) is None

    def test_missing_cwd_returns_none(self):
        cfg = ExecutorConfig(sandbox_mode=SandboxMode.READ_ONLY)
        assert resolve_file_system_sandbox(cfg, None) is None
        assert resolve_file_system_sandbox(cfg, "") is None

    def test_read_only_expansion(self):
        cfg = ExecutorConfig(sandbox_mode=SandboxMode.READ_ONLY)
        ctx = resolve_file_system_sandbox(cfg, CWD)
        assert ctx is not None
        assert ctx.permissions.type == "managed"
        assert ctx.permissions.network is NetworkSandboxPolicy.RESTRICTED
        # 全盘可读、无处可写（对位 codex PermissionProfile::read_only）
        assert len(_entries(ctx)) == 1
        assert _entries(ctx)[0].path == ExecFileSystemPath.root()
        assert _entries(ctx)[0].access is FileSystemAccessMode.READ

    def test_workspace_write_defaults(self):
        cfg = ExecutorConfig(sandbox_mode=SandboxMode.WORKSPACE_WRITE)
        ctx = resolve_file_system_sandbox(cfg, CWD)
        assert ctx is not None
        # 网络默认受限（放行归 network_proxy 名单，不靠档位默认开）
        assert ctx.permissions.network is NetworkSandboxPolicy.RESTRICTED
        paths = [e.path for e in _entries(ctx)]
        # 基座：全盘只读 + 项目根可写 + /tmp + $TMPDIR + .git/.nova 降只读
        assert ExecFileSystemPath.root() in paths
        assert ExecFileSystemPath.project_roots() in paths
        assert ExecFileSystemPath.slash_tmp() in paths
        assert ExecFileSystemPath.tmpdir() in paths
        assert ExecFileSystemPath.project_roots(".git") in paths
        assert ExecFileSystemPath.project_roots(".nova") in paths

    def test_workspace_write_writable_roots_appended(self):
        cfg = ExecutorConfig(
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
            sandbox_workspace_write=SandboxWorkspaceWriteConfig(
                writable_roots=["/data"]
            ),
        )
        ctx = resolve_file_system_sandbox(cfg, CWD)
        assert ctx is not None
        extra = [e for e in _entries(ctx) if e.access is FileSystemAccessMode.WRITE]
        assert ExecFileSystemPath.of_path("/data") in [e.path for e in extra]

    def test_workspace_write_network_access(self):
        cfg = ExecutorConfig(
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
            sandbox_workspace_write=SandboxWorkspaceWriteConfig(network_access=True),
        )
        ctx = resolve_file_system_sandbox(cfg, CWD)
        assert ctx is not None
        assert ctx.permissions.network is NetworkSandboxPolicy.ENABLED

    def test_exclude_tmp_knobs(self):
        cfg = ExecutorConfig(
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
            sandbox_workspace_write=SandboxWorkspaceWriteConfig(
                exclude_slash_tmp=True, exclude_tmpdir_env_var=True
            ),
        )
        ctx = resolve_file_system_sandbox(cfg, CWD)
        assert ctx is not None
        paths = [e.path for e in _entries(ctx)]
        assert ExecFileSystemPath.slash_tmp() not in paths
        assert ExecFileSystemPath.tmpdir() not in paths
        # 其余条目不受影响
        assert ExecFileSystemPath.project_roots() in paths


class TestNetworkProxy:
    def test_absent_returns_none(self):
        assert resolve_network_proxy(ExecutorConfig()) is None

    def test_disabled_returns_none(self):
        cfg = ExecutorConfig(network_proxy=NetworkProxySettings(enabled=False))
        assert resolve_network_proxy(cfg) is None

    def test_enabled_expands_domains(self):
        cfg = ExecutorConfig(
            network_proxy=NetworkProxySettings(
                enabled=True,
                allowed_domains=["*.example.com"],
                denied_domains=["evil.example.com"],
            )
        )
        launch = resolve_network_proxy(cfg)
        assert launch is not None
        assert launch.proxy.enabled is True
        assert launch.proxy.mode is NetworkMode.PROXY
        entries = launch.proxy.domains.entries
        # deny 在前（拒绝优先的防御性排序）
        assert [(e.domain, e.permission.value) for e in entries] == [
            ("evil.example.com", "deny"),
            ("*.example.com", "allow"),
        ]

    def test_empty_domains_omitted(self):
        cfg = ExecutorConfig(network_proxy=NetworkProxySettings(enabled=True))
        launch = resolve_network_proxy(cfg)
        assert launch is not None
        assert launch.proxy.domains is None

    def test_wire_shape_pin(self):
        """wire dump 对位 rust serde camelCase（RemoteNetworkProxyLaunchConfig）"""
        cfg = ExecutorConfig(
            network_proxy=NetworkProxySettings(
                enabled=True, allowed_domains=["example.com"]
            )
        )
        wire = resolve_network_proxy(cfg).model_dump(by_alias=True, exclude_none=True)
        assert wire == {
            "proxy": {
                "enabled": True,
                "enableSocks5": False,
                "enableSocks5Udp": False,
                "allowUpstreamProxy": False,
                "dangerouslyAllowAllUnixSockets": False,
                "mode": "proxy",
                "domains": {
                    "entries": [{"domain": "example.com", "permission": "allow"}]
                },
                "allowLocalBinding": False,
            },
            "auditMetadata": {},
        }


class TestAskBehavior:
    def test_never_downgrades_to_deny(self):
        assert resolve_ask_behavior(ApprovalPolicy.NEVER, ui_available=True) == "deny"

    def test_no_ui_downgrades_to_deny(self):
        for policy in ApprovalPolicy:
            assert resolve_ask_behavior(policy, ui_available=False) == "deny"

    def test_ask_when_policy_allows_and_ui_available(self):
        assert (
            resolve_ask_behavior(ApprovalPolicy.ON_REQUEST, ui_available=True) == "ask"
        )
        assert (
            resolve_ask_behavior(ApprovalPolicy.ON_FAILURE, ui_available=True) == "ask"
        )


class TestComposite:
    def test_resolve_execution_policy(self):
        cfg = ExecutorConfig(
            sandbox_mode=SandboxMode.WORKSPACE_WRITE,
            network_proxy=NetworkProxySettings(enabled=True),
            approval_policy=ApprovalPolicy.NEVER,
        )
        resolved = resolve_execution_policy(cfg, CWD)
        assert resolved.sandbox is not None
        assert resolved.network_proxy is not None
        assert resolved.approval_policy is ApprovalPolicy.NEVER
