"""权限套餐族金标对跑（A2b——codex 锚点 39f4277087 全量移植）。

移植源（逐例对位，语义断言不改）：
- `core/src/config/permissions_tests.rs`（642 行，12 例——
  `active_arg0_helper_dir_*` 属 apply_patch 会话别名面，不在套餐族范围，
  不移植）；
- `core/src/config/permission_path_tests.rs`（542 行，9 例）；
- `core/src/config/permission_profile_selection_tests.rs`（96 行，2 例）；
- `core/src/config/config_tests.rs` 套餐相关用例（TOML 词汇/mitm/extends/
  环/内置/未注册/警告族，18 例）。

实现住所：`nova_protocol/exec_server_permissions/` 包（模块边界对位 codex
——path_uri/toml/proxy_config/runtime/compile/selection）。

设计差登记（**用户拍板的退化语义钉**，非缺口）：
- D-钉1 物化根集合 = {cwd} ∪ 配置显式根。codex 两阶段物化（编译期配置根
  `with_materialized_project_roots_for_path_uris` + 运行期会话根
  `materialize_project_roots_with_workspace_roots`），nova 无"中途批准
  加根"流，坍缩为编译期一次物化——因此编译产物在 codex 编译形状之上
  追加 cwd 衍生的具体条目（符号条目仍保留，服务端经 policyContext 最终
  解析，两通道同集合）。凡 codex 断言编译后条目清单的用例，期望值按钉
  补齐 cwd 衍生条目。

断言口径适配（架构分层差异，非语义缺口）：
- codex 的 `ReadDenyMatcher`/`can_write_path`/`resolve_access` 执法断言
  归 Rust 服务端（exec-server-protocol-core 同型移植件），本侧为**编译
  产物条目级断言**（条目集合等价 ⇒ 执法结果等价）；
- codex 断言 `legacy_sandbox_policy()` 投影（SandboxPolicy 兼容层）的
  用例，本侧不断言该投影（legacy 兼容面未移植）；
- `permission_profile_catalog`（目录/allowed 判定）依赖托管需求整机
  （SandboxModeRequirement 族），不在本批范围——相关用例只断言编译产物
  部分（见 `permissions_profiles_allow_direct_write_roots_outside_workspace_root`）。
- `non_unicode_home_is_rejected_by_the_shared_compiler`：Python str 边界
  不存在非 Unicode 输入；对位保障落 `PermissionPathUri.parse` 的严格
  UTF-8 解码（非法 `%XX` 序列即拒）——以编码形态移植。

xfail 登记：**零**（全量移植，无语义缺口）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass

import pytest
from nova_protocol import ExecutorConfig
from nova_protocol.exec_server_permissions import (
    BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
    ActivePermissionProfile,
    NetworkDomainPermissionsToml,
    NetworkMitmActionToml,
    NetworkMitmHookToml,
    NetworkMitmToml,
    NetworkToml,
    NetworkUnixSocketPermissionsToml,
    PermissionNetworkMode,
    PermissionProfileResolutionError,
    PermissionProfileResolutionErrorKind,
    PermissionProfileToml,
    PermissionRequirementsToml,
    PermissionsToml,
    ProfileNetworkProxyConfig,
    ProxyNetworkDomainPermissionEntry,
    ProxyNetworkDomainPermissions,
    WorkspaceRootsToml,
    WorkspaceWriteSettings,
    compile_permission_profile,
    network_proxy_config_from_profile_network,
    project_roots_glob_pattern,
    resolve_config_permission_selection,
    resolve_permission_profile,
    resolve_permission_profile_selection,
)
from nova_protocol.exec_server_permissions.compile import (
    _compile_read_write_glob_path,
    _unbounded_unreadable_globstar_paths,
    _unsupported_read_write_glob_paths,
    _validate_glob_scan_max_depth,
    contains_glob_chars_for_platform,
)
from nova_protocol.exec_server_permissions.path_uri import (
    PermissionPathContext,
    PermissionPathConvention,
    PermissionPathError,
    PermissionPathUri,
)
from nova_protocol.exec_server_permissions.toml import (
    FileSystemAccessModeToml,
    FilesystemPermissionsToml,
    FilesystemPermissionToml,
)
from nova_protocol.exec_server_wire import (
    ExecFileSystemPath,
    ExecFileSystemSandboxEntry,
    ExecFileSystemSpecialPath,
    FileSystemAccessMode,
    NetworkDomainPermission,
    NetworkSandboxPolicy,
)
from pydantic import ValidationError

_READ = FileSystemAccessMode.READ
_WRITE = FileSystemAccessMode.WRITE
_DENY = FileSystemAccessMode.DENY
_ALLOW = NetworkDomainPermission.ALLOW


# =============================================================================
# 测试辅助（对位 permission_path_tests.rs:32-164 的 ExecutorPathFacts 族）
# =============================================================================


def _path_entry(
    uri: str, access: FileSystemAccessMode, *, skip: bool = False
) -> ExecFileSystemSandboxEntry:
    """具体路径条目（对位 FileSystemSandboxEntry::new(PathUri)）"""
    return ExecFileSystemSandboxEntry(
        path=ExecFileSystemPath(type="path", path=uri),
        access=access,
        missing_path_behavior="skip" if skip else None,
    )


def _special_entry(
    kind: str,
    access: FileSystemAccessMode,
    *,
    subpath: str | None = None,
    path: str | None = None,
    skip: bool = False,
) -> ExecFileSystemSandboxEntry:
    return ExecFileSystemSandboxEntry(
        path=ExecFileSystemPath(
            type="special",
            value=ExecFileSystemSpecialPath(kind=kind, subpath=subpath, path=path),
        ),
        access=access,
        missing_path_behavior="skip" if skip else None,
    )


def _glob_entry(
    pattern: str, access: FileSystemAccessMode = _DENY
) -> ExecFileSystemSandboxEntry:
    """对位 permission_path_tests.rs:166-173 `deny_glob_entry`"""
    return ExecFileSystemSandboxEntry(
        path=ExecFileSystemPath(type="glob_pattern", pattern=pattern), access=access
    )


@dataclass
class _ExecutorPathFacts:
    """对位 permission_path_tests.rs:32-64"""

    base_dir: PermissionPathUri
    user_home_dir: PermissionPathUri | None

    def context(self) -> PermissionPathContext:
        convention = self.base_dir.infer_path_convention()
        assert convention is not None
        return PermissionPathContext(convention, self.base_dir, self.user_home_dir)

    def compile(self, permissions: PermissionsToml):
        return compile_permission_profile(
            permissions, "remote", self.context(), None, []
        )


def _executor_contexts() -> list[_ExecutorPathFacts]:
    """四种 executor 路径语境（逐字对位 permission_path_tests.rs:66-81）"""
    return [
        _ExecutorPathFacts(PermissionPathUri.parse(cwd), PermissionPathUri.parse(home))
        for cwd, home in [
            ("file:///workspace/project", "file:///home/executor"),
            ("file:///Users/executor/project", "file:///Users/executor"),
            ("file:///C:/workspace/project", "file:///C:/Users/executor"),
            ("file://server/share/project", "file://server/share/users/executor"),
        ]
    ]


def _permissions_with_rule(
    path: str, permission: FilesystemPermissionToml
) -> PermissionsToml:
    """对位 permission_path_tests.rs:83-99"""
    return PermissionsToml(
        {
            "remote": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(entries={path: permission})
            )
        }
    )


def _scoped(
    entries: list[tuple[str, FileSystemAccessMode]],
) -> FilesystemPermissionToml:
    """对位 permission_path_tests.rs:101-108"""
    return FilesystemPermissionToml.scoped(dict(entries))


def _assert_executor_rule(
    permissions: PermissionsToml,
    context: PermissionPathContext,
    expected: ExecFileSystemSandboxEntry,
):
    """对位 permission_path_tests.rs:134-164 `assert_executor_rule`"""
    warnings: list[str] = []
    compiled = compile_permission_profile(
        permissions, "remote", context, None, warnings
    )
    assert compiled.permission_profile.file_system.type == "restricted"
    assert compiled.permission_profile.file_system.entries == [expected]
    assert compiled.permission_profile.network is NetworkSandboxPolicy.RESTRICTED
    assert compiled.workspace_roots == ()
    assert warnings == []
    return compiled


def _native(uri: PermissionPathUri) -> str:
    return uri.inferred_native_path_string()


# =============================================================================
# 对位 core/src/config/permissions_tests.rs
# =============================================================================


def test_windows_verbatim_path_prefix_does_not_count_as_glob_syntax():
    """对位 permissions_tests.rs:41-51"""
    assert not contains_glob_chars_for_platform(
        r"\\?\D:\c\x\worktrees\2508\swift-base", True
    )
    assert contains_glob_chars_for_platform(r"\\?\D:\c\x\worktrees\2508\**\*.env", True)


def test_network_toml_ignores_legacy_network_list_keys():
    """对位 permissions_tests.rs:53-63——legacy 平铺名单键忽略（serde 层面
    不拒未知键；对位 pydantic 默认 extra=ignore）"""
    parsed = NetworkToml.model_validate({"allowed_domains": ["openai.com"]})
    assert parsed == NetworkToml()


def test_network_permission_containers_project_allowed_and_denied_entries():
    """对位 permissions_tests.rs:65-121"""
    domains = NetworkDomainPermissionsToml(
        {
            "*.openai.com": _ALLOW,
            "api.example.com": _ALLOW,
            "blocked.example.com": NetworkDomainPermission.DENY,
        }
    )
    unix_sockets = NetworkUnixSocketPermissionsToml(
        {
            "/tmp/example.sock": _ALLOW,
            "/tmp/ignored.sock": NetworkDomainPermission.DENY,
        }
    )
    assert domains.allowed_domains() == ["*.openai.com", "api.example.com"]
    assert domains.denied_domains() == ["blocked.example.com"]
    assert (
        NetworkDomainPermissionsToml({"api.example.com": _ALLOW}).denied_domains()
        is None
    )
    assert unix_sockets.allow_unix_sockets() == ["/tmp/example.sock"]


def test_network_toml_overlays_unix_socket_permissions_by_path():
    """对位 permissions_tests.rs:123-180——两次叠加按路径覆盖"""
    config = ProfileNetworkProxyConfig()
    NetworkToml(
        unix_sockets=NetworkUnixSocketPermissionsToml(
            {"/tmp/base.sock": _ALLOW, "/tmp/override.sock": _ALLOW}
        )
    ).apply_to_network_proxy_config(config)
    NetworkToml(
        unix_sockets=NetworkUnixSocketPermissionsToml(
            {
                "/tmp/extra.sock": _ALLOW,
                "/tmp/override.sock": NetworkDomainPermission.DENY,
            }
        )
    ).apply_to_network_proxy_config(config)
    assert config.unix_sockets == {
        "/tmp/base.sock": _ALLOW,
        "/tmp/extra.sock": _ALLOW,
        "/tmp/override.sock": NetworkDomainPermission.DENY,
    }


def test_permissions_profiles_resolve_extends_parent_first_with_child_overrides():
    """对位 permissions_tests.rs:182-269——父先子后合并全矩阵（含域名归一
    后子键覆盖、scoped 表深合并、选中套餐 extends 元数据保留）"""
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[base]
description = "Base profile"

[base.filesystem]
glob_scan_max_depth = 1
"/tmp/base" = "read"
"/tmp/shared" = "read"

[base.filesystem.":project_roots"]
"**/*.env" = "deny"
docs = "read"

[base.network]
enabled = true

[base.network.domains]
"base.example.com" = "allow"
"SHARED.EXAMPLE.COM." = "deny"

[base.network.unix_sockets]
"/tmp/base.sock" = "allow"
"/tmp/blocked.sock" = "deny"

[child]
extends = "base"

[child.filesystem]
glob_scan_max_depth = 3
"/tmp/shared" = "write"

[child.filesystem.":project_roots"]
docs = "write"
src = "read"

[child.network]
enabled = false
allow_local_binding = true

[child.network.domains]
"child.example.com" = "allow"
"shared.example.com" = "allow"

[child.network.unix_sockets]
"/tmp/child.sock" = "allow"
"""))

    resolved = permissions.resolve_profile("child", lambda _name: None)
    expected = PermissionProfileToml.model_validate(tomllib.loads("""
extends = "base"

[filesystem]
glob_scan_max_depth = 3
"/tmp/base" = "read"
"/tmp/shared" = "write"

[filesystem.":project_roots"]
"**/*.env" = "deny"
docs = "write"
src = "read"

[network]
enabled = false
allow_local_binding = true

[network.domains]
"base.example.com" = "allow"
"child.example.com" = "allow"
"shared.example.com" = "allow"

[network.unix_sockets]
"/tmp/base.sock" = "allow"
"/tmp/blocked.sock" = "deny"
"/tmp/child.sock" = "allow"
"""))
    assert resolved == expected


def test_permissions_profiles_reject_undefined_extends_parent():
    """对位 permissions_tests.rs:271-289"""
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[child]
extends = "base"
"""))
    with pytest.raises(PermissionProfileResolutionError) as exc_info:
        permissions.resolve_profile("child", lambda _name: None)
    assert exc_info.value.kind is PermissionProfileResolutionErrorKind.UNDEFINED_PARENT
    assert (
        str(exc_info.value)
        == "permissions profile `child` extends undefined profile `base`"
    )


def test_permissions_profiles_reject_unsupported_builtin_extends_parent():
    """对位 permissions_tests.rs:291-309——`:danger-full-access` 不可作父"""
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[child]
extends = ":danger-full-access"
"""))
    with pytest.raises(PermissionProfileResolutionError) as exc_info:
        permissions.resolve_profile("child", lambda _name: None)
    assert (
        exc_info.value.kind
        is PermissionProfileResolutionErrorKind.UNSUPPORTED_BUILTIN_PARENT
    )
    assert (
        str(exc_info.value)
        == "permissions profile `child` cannot extend unsupported built-in "
        "profile `:danger-full-access`"
    )


def test_permissions_profiles_reject_extends_cycles():
    """对位 permissions_tests.rs:311-332——环检测带环路径"""
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[alpha]
extends = "beta"

[beta]
extends = "alpha"
"""))
    with pytest.raises(PermissionProfileResolutionError) as exc_info:
        permissions.resolve_profile("alpha", lambda _name: None)
    assert exc_info.value.kind is PermissionProfileResolutionErrorKind.CYCLE
    assert exc_info.value.cycle == ["alpha", "beta", "alpha"]
    assert (
        str(exc_info.value)
        == "permissions profile inheritance cycle detected: alpha -> beta -> alpha"
    )


def test_profile_network_proxy_config_keeps_proxy_disabled_for_bare_network_access():
    """对位 permissions_tests.rs:334-342——套餐 network.enabled 只控沙箱
    网络位，不启动托管代理"""
    config = network_proxy_config_from_profile_network(NetworkToml(enabled=True))
    assert not config.enabled


def test_profile_network_proxy_config_keeps_proxy_disabled_for_proxy_policy():
    """对位 permissions_tests.rs:344-371"""
    config = network_proxy_config_from_profile_network(
        NetworkToml(
            enabled=True,
            proxy_url="http://127.0.0.1:43128",
            enable_socks5=False,
            domains=NetworkDomainPermissionsToml({"openai.com": _ALLOW}),
        )
    )
    assert not config.enabled
    assert config.proxy_url == "http://127.0.0.1:43128"
    assert not config.enable_socks5
    assert config.domains == ProxyNetworkDomainPermissions(
        entries=[
            ProxyNetworkDomainPermissionEntry(pattern="openai.com", permission=_ALLOW)
        ]
    )


def test_compile_permission_profile_resolves_enabled_workspace_roots():
    """对位 permissions_tests.rs:373-413——enabled_roots 只收 true 键，
    相对根对基准目录解析（TempDir 主机路径 → 固定 URI 语境，纯计算等价）"""
    permissions = PermissionsToml(
        {
            "workspace": PermissionProfileToml(
                workspace_roots=WorkspaceRootsToml({"backend": True, "disabled": False})
            )
        }
    )
    context = PermissionPathContext.of_uris(
        PermissionPathConvention.POSIX, "file:///workspace/project", None
    )
    compiled = compile_permission_profile(permissions, "workspace", context, None, [])
    assert [str(root) for root in compiled.workspace_roots] == [
        "file:///workspace/project/backend"
    ]


def test_legacy_project_roots_restrictions_do_not_fail_open():
    """对位 permissions_tests.rs:415-489——`none` legacy 别名的 deny 语义
    物化为具体根拒绝（执法断言 → 条目断言，见文件头）。

    codex 三档断言：resolve_access(cwd)==Deny（read_deny/write_deny）、
    resolve_access(docs)==Read（write_read）；本侧断言编译条目清单——
    cwd 拒绝条目在位即不会 fail-open。
    """
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[read_deny.filesystem]
":root" = "read"
":project_roots" = "none"

[write_deny.filesystem]
":root" = "write"
":project_roots" = "none"

[write_read.filesystem]
":root" = "write"

[write_read.filesystem.":project_roots"]
docs = "read"
"""))
    cwd = PermissionPathUri.parse("file:///workspace/project")
    docs = cwd.join("docs")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)

    read_deny = compile_permission_profile(
        permissions, "read_deny", context, None, []
    ).permission_profile
    # `:project_roots` < `:root`（BTreeMap 键序）；cwd 拒绝条目随钉物化追加
    assert read_deny.file_system.entries == [
        _special_entry("project_roots", _DENY),
        _special_entry("root", _READ),
        _path_entry(str(cwd), _DENY),
    ]

    write_deny = compile_permission_profile(
        permissions, "write_deny", context, None, []
    ).permission_profile
    assert write_deny.file_system.entries == [
        _special_entry("project_roots", _DENY),
        _special_entry("root", _WRITE),
        _path_entry(str(cwd), _DENY),
    ]

    write_read = compile_permission_profile(
        permissions, "write_read", context, None, []
    ).permission_profile
    assert write_read.file_system.entries == [
        _special_entry("project_roots", _READ, subpath="docs"),
        _special_entry("root", _WRITE),
        _path_entry(str(docs), _READ),
    ]


def test_read_write_glob_warnings_skip_supported_deny_read_globs_and_trailing_subpaths():
    """对位 permissions_tests.rs:491-531——deny glob 是受支持的 deny-read
    规则，只有 read/write glob 告警"""
    filesystem = FilesystemPermissionsToml(
        entries={
            "/tmp/**/*.log": FilesystemPermissionToml.access(_READ),
            "/tmp/cache/**": FilesystemPermissionToml.access(_WRITE),
            ":workspace_roots": FilesystemPermissionToml.scoped(
                {
                    "**/*.env": _DENY,
                    "docs/**": _READ,
                    "src/**/*.rs": _WRITE,
                }
            ),
        }
    )
    context = PermissionPathContext(PermissionPathConvention.POSIX, None, None)
    assert _unsupported_read_write_glob_paths(filesystem, context) == [
        "/tmp/**/*.log",
        ":workspace_roots/src/**/*.rs",
    ]


def test_unreadable_globstar_warning_is_suppressed_when_scan_depth_is_configured():
    """对位 permissions_tests.rs:533-559"""
    filesystem = FilesystemPermissionsToml(
        entries={
            ":workspace_roots": FilesystemPermissionToml.scoped(
                {"**/*.env": _DENY, "*.pem": _DENY}
            )
        }
    )
    assert _unbounded_unreadable_globstar_paths(filesystem) == [
        ":workspace_roots/**/*.env"
    ]
    configured = filesystem.model_copy(update={"glob_scan_max_depth": 2})
    assert _unbounded_unreadable_globstar_paths(configured) == []


def test_glob_scan_max_depth_must_be_positive():
    """对位 permissions_tests.rs:561-572"""
    with pytest.raises(ValueError) as exc_info:
        _validate_glob_scan_max_depth(0)
    assert str(exc_info.value) == "glob_scan_max_depth must be at least 1"
    assert _validate_glob_scan_max_depth(2) == 2
    assert _validate_glob_scan_max_depth(None) is None


def test_read_write_trailing_glob_suffix_compiles_as_subpath():
    """对位 permissions_tests.rs:574-621——尾部 `/**` 编译为子树路径而非
    glob 模式（无语境根：无 cwd/无套餐根 → 无物化追加，与 codex 一致）"""
    permissions = PermissionsToml(
        {
            "workspace": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":workspace_roots": FilesystemPermissionToml.scoped(
                            {"docs/**": _READ}
                        )
                    }
                )
            )
        }
    )
    context = PermissionPathContext(PermissionPathConvention.POSIX, None, None)
    compiled = compile_permission_profile(permissions, "workspace", context, None, [])
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("project_roots", _READ, subpath="docs")
    ]


def test_read_write_glob_patterns_still_reject_non_subpath_globs():
    """对位 permissions_tests.rs:623-642"""
    context = PermissionPathContext(PermissionPathConvention.POSIX, None, None)
    with pytest.raises(PermissionPathError) as exc_info:
        _compile_read_write_glob_path("src/**/*.rs", _READ, context)
    assert "filesystem glob path `src/**/*.rs` only supports `deny` access" in str(
        exc_info.value
    )


# =============================================================================
# 对位 core/src/config/permission_path_tests.rs
# =============================================================================


def test_compiler_uses_shared_resolution_for_literal_and_glob_rules():
    """对位 permission_path_tests.rs:175-207——字面子树与 deny glob 走同一
    共享解析器，四种 executor 语境全量"""
    for facts in _executor_contexts():
        home = facts.user_home_dir
        assert home is not None
        root = home.join("shared")
        literal = _path_entry(str(root), _WRITE)
        glob = _glob_entry(_native(home.join("shared/*.env")))
        for input_path, permission, expected in [
            ("~/shared/**", FilesystemPermissionToml.access(_WRITE), literal),
            ("~/shared/*.env", FilesystemPermissionToml.access(_DENY), glob),
            (_native(home), _scoped([("shared", _WRITE)]), literal),
            (_native(home), _scoped([("shared/*.env", _DENY)]), glob),
        ]:
            _assert_executor_rule(
                _permissions_with_rule(input_path, permission),
                facts.context(),
                expected,
            )


def test_scoped_home_rules_resolve_and_deny_the_supplied_home():
    """对位 permission_path_tests.rs:209-252——作用域内的 home 相对子路径
    逃逸作用域、对供应的 home 解析（执法断言 → 条目断言）；`..` 逃逸拒收"""
    for facts in _executor_contexts():
        home = facts.user_home_dir
        assert home is not None
        home_glob = (
            "~/private/*.env"
            if facts.context().convention is PermissionPathConvention.POSIX
            else "~\\private\\*.env"
        )
        for outer in [
            _native(facts.base_dir),
            ":workspace_roots",
            ":project_roots",
        ]:
            for subpath, resolved in [
                ("~", home),
                ("~/private", home.join("private")),
                (home_glob, home.join("private/*.env")),
            ]:
                expected = (
                    _glob_entry(_native(resolved))
                    if "*" in subpath
                    else _path_entry(str(resolved), _DENY)
                )
                _assert_executor_rule(
                    _permissions_with_rule(outer, _scoped([(subpath, _DENY)])),
                    facts.context(),
                    expected,
                )
            for subpath in ("~/../private", "~/child/../private"):
                with pytest.raises(PermissionPathError):
                    facts.compile(
                        _permissions_with_rule(outer, _scoped([(subpath, _DENY)]))
                    )


def test_inherited_workspace_roots_apply_to_executor_policy_and_read_denials():
    """对位 permission_path_tests.rs:254-308——继承链 + workspace_roots
    合并（子 false 覆盖父 true）+ deny glob 随根物化（条目断言按钉补齐
    cwd 衍生条目）"""
    permissions = PermissionsToml.model_validate(tomllib.loads("""
[base]
extends = ":read-only"
[base.workspace_roots]
"../shared" = true
"~/scratch" = true
[remote]
extends = "base"
[remote.workspace_roots]
"../shared" = false
"./profile-root" = true
[remote.filesystem]
glob_scan_max_depth = 3
[remote.filesystem.":workspace_roots"]
"." = "write"
"private/*.env" = "deny"
[remote.network]
enabled = true
"""))
    for facts in _executor_contexts():
        compiled = facts.compile(permissions)
        home = facts.user_home_dir
        assert home is not None
        profile_root = facts.base_dir.join("profile-root")
        scratch = home.join("scratch")
        assert compiled.workspace_roots == (profile_root, scratch)
        assert compiled.permission_profile.network is NetworkSandboxPolicy.ENABLED
        assert compiled.permission_profile.file_system.glob_scan_max_depth == 3
        # 物化根集合 = {cwd} ∪ 套餐根（钉 D-钉1）；符号条目保留 + 每根追加
        # 具体写条目与 deny glob（对位 codex 编译形状 + 运行期物化的坍缩）
        session_roots = [facts.base_dir, profile_root, scratch]
        assert compiled.permission_profile.file_system.entries == [
            _special_entry("root", _READ),
            _special_entry("project_roots", _WRITE),
            _glob_entry(project_roots_glob_pattern("private/*.env")),
            *[_path_entry(str(root), _WRITE) for root in session_roots],
            *[
                _glob_entry(_native(root.join("private/*.env")))
                for root in session_roots
            ],
        ]


def test_interior_dot_workspace_glob_fails_closed_for_every_path_convention():
    """对位 permission_path_tests.rs:310-352——内部 `.` 的 glob 拼写保留
    符号形态（编译期），物化时严格拒绝降级为整根 deny（fail-closed）"""
    permissions = _permissions_with_rule(
        ":workspace_roots",
        _scoped([(".", _WRITE), ("private/./*.env", _DENY)]),
    )
    for facts in _executor_contexts():
        compiled = facts.compile(permissions)
        # codex 编译形状（符号保留）+ 钉 D-钉1 的 cwd 物化：
        # `private/./*.env` 含 `.` 段 → resolve_scoped_workspace_path 拒 →
        # deny 兜底为整根拒绝（对位 protocol permissions.rs:1316-1320）
        assert compiled.permission_profile.file_system.entries == [
            _special_entry("project_roots", _WRITE),
            _glob_entry("codex-project-roots://private/./*.env"),
            _path_entry(str(facts.base_dir), _WRITE),
            _path_entry(str(facts.base_dir), _DENY),
        ]
        assert compiled.permission_profile.network is NetworkSandboxPolicy.RESTRICTED


def test_permission_rules_require_absolute_paths_and_descendant_subpaths():
    """对位 permission_path_tests.rs:354-383——权限规则不收相对路径，
    缺失 executor home 不落回宿主 home"""
    for facts in _executor_contexts():
        facts.user_home_dir = None
        for input_path in [
            "relative",
            "./relative",
            "~",
            "~/private",
            "C:private",
            r"\private",
            # 原始文本校验先于 URI 解析（`a:b` 段含冒号即拒）
            r"C:\base\a:b\..\private",
        ]:
            with pytest.raises(PermissionPathError):
                facts.compile(
                    _permissions_with_rule(
                        input_path, FilesystemPermissionToml.access(_READ)
                    )
                )
        for outer in [_native(facts.base_dir), ":workspace_roots"]:
            for subpath in ("../private", "./private", "child/../private"):
                with pytest.raises(PermissionPathError):
                    facts.compile(
                        _permissions_with_rule(outer, _scoped([(subpath, _DENY)]))
                    )


def test_executor_scoped_globs_preserve_native_separator_spelling():
    """对位 permission_path_tests.rs:385-398——Windows 语境下作用域 glob
    的分隔符拼写原样进符号模式（钉 D-钉1：cwd 物化追加分隔符归一后的
    具体模式条目——对位 codex 运行期物化形状）"""
    for facts in _executor_contexts():
        if facts.context().convention is not PermissionPathConvention.WINDOWS:
            continue
        for subpath in ["secrets/*.env", r"secrets\*.env", r"nested/secrets\*.env"]:
            warnings: list[str] = []
            compiled = compile_permission_profile(
                _permissions_with_rule(":workspace_roots", _scoped([(subpath, _DENY)])),
                "remote",
                facts.context(),
                None,
                warnings,
            )
            assert compiled.permission_profile.file_system.entries == [
                _glob_entry(project_roots_glob_pattern(subpath)),
                _glob_entry(_native(facts.base_dir.join(subpath))),
            ]
            assert warnings == []


def _rust_posix_components_all_normal(path: str) -> bool:
    """codex 断言参照物：`Path::components().all(Normal)` 的 POSIX 语义
    （permission_path_tests.rs:430-433）——RootDir/首段 CurDir/任何
    ParentDir 皆非 Normal；内部 `.` 与重复/尾随分隔符被 components 跳过"""
    if path == "" or path.startswith("/"):
        return False
    segments = [segment for segment in path.split("/") if segment != ""]
    for index, segment in enumerate(segments):
        if segment == "..":
            return False
        if segment == "." and index == 0:
            return False
    return True


def test_native_path_grammar_preserves_component_validation_and_spelling():
    """对位 permission_path_tests.rs:411-442——`relative_subpath` 验收面与
    Rust `Path::components` 全 Normal 判定逐一对齐（通过后保留拼写）。

    codex 用宿主约定跑本矩阵（本仓 CI 宿主 = macOS/Linux → POSIX）；Windows
    专属分支（驱动器前缀拒收，permission_path.rs:40-45）以显式 WINDOWS
    语境补测。
    """
    from nova_protocol.exec_server_permissions.compile import _relative_subpath

    context = PermissionPathContext(PermissionPathConvention.POSIX, None, None)
    for input_path in [
        "",
        ".",
        "..",
        "./child",
        "../child",
        "child/../other",
        "child/./other",
        "child//other/",
        "~/child",
        "~\\child",
        "C:child",
        "/child",
        "\\child",
        "child/.../leaf",
    ]:
        native_accepts = input_path != "" and _rust_posix_components_all_normal(
            input_path
        )
        try:
            actual = _relative_subpath(input_path, context)
        except PermissionPathError:
            assert not native_accepts, input_path
        else:
            assert native_accepts, input_path
            assert actual == input_path

    windows_context = PermissionPathContext(
        PermissionPathConvention.WINDOWS, None, None
    )
    # Windows 专属：驱动器相对前缀拒收（对位 permission_path.rs:40-45）
    with pytest.raises(PermissionPathError):
        _relative_subpath("C:child", windows_context)
    with pytest.raises(PermissionPathError):
        _relative_subpath("child\\..\\other", windows_context)
    assert _relative_subpath("child\\leaf", windows_context) == "child\\leaf"


def test_non_unicode_home_is_rejected_by_the_shared_compiler():
    """对位 permission_path_tests.rs:444-461（`#[cfg(unix)]`）——以编码形态
    移植：非法 UTF-8 的 `%FF` 序列在 `PermissionPathUri.parse` 即拒
    （fail-closed 对位 `validate_config_path` 的 UTF-8 检查点；Python str
    边界不存在非 Unicode 原生路径输入）"""
    with pytest.raises(PermissionPathError):
        PermissionPathUri.parse("file:///home/native-%FF")


def test_deny_globs_validate_literal_home_and_scoped_directories():
    """对位 permission_path_tests.rs:463-479——含 glob 元字符的字面目录
    （`sam[1]`）不得嵌入 glob（拒绝转义歧义）"""
    for facts in _executor_contexts():
        directory = facts.base_dir.join("sam[1]")
        facts.user_home_dir = directory
        for outer, permission in [
            ("~/private/*.key", FilesystemPermissionToml.access(_DENY)),
            ("~/private", _scoped([("*.key", _DENY)])),
            (
                _native(directory),
                _scoped([("private/*.key", _DENY)]),
            ),
        ]:
            with pytest.raises(PermissionPathError):
                facts.compile(_permissions_with_rule(outer, permission))


def test_compiler_applies_deny_globs_safely_to_literal_profile_workspace_roots():
    """对位 permission_path_tests.rs:481-509——字面套餐根含 glob 元字符
    时，不可失败物化器兜底为整根 deny（fail-closed）；钉 D-钉1 补齐 cwd
    衍生条目（cwd 为干净目录 → deny glob 正常物化为模式条目）"""
    for facts in _executor_contexts():
        root = facts.base_dir.join("sam[1]")
        permissions = _permissions_with_rule(
            ":workspace_roots", _scoped([(".", _WRITE), ("private/*.key", _DENY)])
        )
        permissions.entries["remote"].workspace_roots = WorkspaceRootsToml(
            {_native(root): True}
        )
        compiled = facts.compile(permissions)
        assert compiled.workspace_roots == (root,)
        assert compiled.permission_profile.file_system.entries == [
            _special_entry("project_roots", _WRITE),
            _glob_entry("codex-project-roots://private/*.key"),
            _path_entry(str(facts.base_dir), _WRITE),
            _path_entry(str(root), _WRITE),
            _glob_entry(_native(facts.base_dir.join("private/*.key"))),
            # `sam[1]` 含 glob 元字符 → validate_glob_directory 拒 → 整根兜底
            _path_entry(str(root), _DENY),
        ]


def test_builtin_workspace_settings_use_executor_roots_in_the_compiled_profile():
    """对位 permission_path_tests.rs:511-542——legacy 设置的根去重后进
    物化集合；网络开关直通；exclude 旋钮摘掉对应符号条目"""
    for facts in _executor_contexts():
        root = facts.base_dir.join("legacy-root")
        settings = WorkspaceWriteSettings(
            writable_roots=(_native(root), _native(root)),
            network_access=True,
            exclude_tmpdir_env_var=True,
            exclude_slash_tmp=True,
        )
        compiled = compile_permission_profile(
            None, ":workspace", facts.context(), settings, []
        )
        assert compiled.workspace_roots == (root,)
        assert compiled.permission_profile.network is NetworkSandboxPolicy.ENABLED
        # 内置 :workspace（exclude 双开 → 无 slash_tmp/tmpdir 条目）+
        # 钉 D-钉1 物化（{cwd} ∪ {legacy-root}）
        session_roots = [facts.base_dir, root]
        assert compiled.permission_profile.file_system.entries == [
            _special_entry("root", _READ),
            _special_entry("project_roots", _WRITE),
            *[
                _special_entry("project_roots", _READ, subpath=name, skip=True)
                for name in (".git", ".agents", ".codex", ".aws")
            ],
            *[_path_entry(str(r), _WRITE) for r in session_roots],
            *[
                _path_entry(str(r.join(name)), _READ, skip=True)
                for name in (".git", ".agents", ".codex", ".aws")
                for r in session_roots
            ],
        ]


# =============================================================================
# 对位 core/src/config/permission_profile_selection_tests.rs
# =============================================================================


def test_resolves_managed_profile_without_compiling_executor_paths():
    """对位 permission_profile_selection_tests.rs:8-70——托管套餐并入目录
    （不编译 executor 路径），allowlist 否决配置缺省回落到托管缺省"""
    configured_profiles = PermissionsToml.model_validate(tomllib.loads(r"""
[configured-windows]
extends = ":workspace"

[configured-windows.workspace_roots]
'C:\Users\agent\workspace' = true

[configured-windows.filesystem]
'C:\Users\agent\workspace' = "write"
"""))
    requirements = PermissionRequirementsToml.model_validate(tomllib.loads(r"""
default_permissions = "managed-windows"

[allowed_permission_profiles]
configured-windows = false
managed-windows = true

[permissions.managed-windows.workspace_roots]
'D:\Managed\workspace' = true

[permissions.managed-windows.filesystem]
'D:\Managed\workspace' = "read"
"""))
    resolved = resolve_permission_profile_selection(
        configured_profiles, "configured-windows", requirements
    )
    assert resolved.profile_id == "managed-windows"
    assert resolved.profiles is not None
    assert set(resolved.profiles.entries) == {
        "configured-windows",
        "managed-windows",
    }
    # 配置套餐原样保留；托管套餐来自需求层（对位 expected_profiles 构造）
    assert (
        resolved.profiles.entries["configured-windows"]
        == configured_profiles.entries["configured-windows"]
    )
    assert resolved.profiles.entries["managed-windows"] == (
        requirements.permissions.profiles["managed-windows"]
    )


def test_rejects_undefined_allowlisted_permission_profile():
    """对位 permission_profile_selection_tests.rs:72-96"""
    requirements = PermissionRequirementsToml.model_validate(tomllib.loads("""
default_permissions = "missing-profile"

[allowed_permission_profiles]
missing-profile = true
"""))
    with pytest.raises(ValueError) as exc_info:
        resolve_permission_profile_selection(None, None, requirements)
    assert (
        str(exc_info.value)
        == "requirements.toml allowed_permission_profiles refers to "
        "undefined profile `missing-profile`"
    )


# =============================================================================
# 对位 core/src/config/config_tests.rs 套餐相关用例
# =============================================================================


def test_config_toml_deserializes_permission_profiles():
    """对位 config_tests.rs:1411-1531——`ExecutorConfig` 对位 ConfigToml
    的承载形状（default_permissions + [permissions] 表）"""
    cfg = ExecutorConfig.model_validate(tomllib.loads("""
default_permissions = "dev"

[permissions.dev]
description = "Day-to-day workspace access."

[permissions.dev.workspace_roots]
"~/code/openai" = true
"~/code/ignored" = false

[permissions.dev.filesystem]
":minimal" = "read"
"/tmp/secret.env" = "deny"

[permissions.dev.filesystem.":workspace_roots"]
"." = "write"
"docs" = "read"

[permissions.dev.network]
enabled = true
proxy_url = "http://127.0.0.1:43128"
enable_socks5 = false
allow_upstream_proxy = false
mode = "full"

[permissions.dev.network.domains]
"openai.com" = "allow"

[permissions.dev.network.mitm.hooks.github_write]
host = "api.github.com"
methods = ["POST", "PUT"]
path_prefixes = ["/repos/openai/"]
action = ["strip_auth"]

[permissions.dev.network.mitm.actions.strip_auth]
strip_request_headers = ["authorization"]
"""))
    assert cfg.default_permissions == "dev"
    assert cfg.permissions is not None
    assert cfg.permissions.entries == {
        "dev": PermissionProfileToml(
            description="Day-to-day workspace access.",
            workspace_roots=WorkspaceRootsToml(
                {"~/code/ignored": False, "~/code/openai": True}
            ),
            filesystem=FilesystemPermissionsToml(
                entries={
                    ":minimal": FilesystemPermissionToml.access(_READ),
                    "/tmp/secret.env": FilesystemPermissionToml.access(_DENY),
                    ":workspace_roots": FilesystemPermissionToml.scoped(
                        {".": _WRITE, "docs": _READ}
                    ),
                }
            ),
            network=NetworkToml(
                enabled=True,
                proxy_url="http://127.0.0.1:43128",
                enable_socks5=False,
                allow_upstream_proxy=False,
                mode=PermissionNetworkMode.FULL,
                domains=NetworkDomainPermissionsToml({"openai.com": _ALLOW}),
                mitm=NetworkMitmToml(
                    hooks={
                        "github_write": NetworkMitmHookToml(
                            host="api.github.com",
                            methods=["POST", "PUT"],
                            path_prefixes=["/repos/openai/"],
                            action=["strip_auth"],
                        )
                    },
                    actions={
                        "strip_auth": NetworkMitmActionToml(
                            strip_request_headers=["authorization"]
                        )
                    },
                ),
            ),
        )
    }


def test_config_toml_rejects_empty_mitm_action_reference_list():
    """对位 config_tests.rs:1533-1557——空 action 引用列表 fail-closed"""
    with pytest.raises(ValidationError) as exc_info:
        ExecutorConfig.model_validate(tomllib.loads("""
default_permissions = "workspace"

[permissions.workspace.network.mitm.hooks.github_write]
host = "api.github.com"
methods = ["POST"]
path_prefixes = ["/repos/openai/"]
action = []

[permissions.workspace.network.mitm.actions.strip_auth]
strip_request_headers = ["authorization"]
"""))
    assert "network.mitm.hooks.github_write.action must not be empty" in str(
        exc_info.value
    )


def test_config_toml_rejects_empty_mitm_action_definition():
    """对位 config_tests.rs:1559-1582——空 action 定义 fail-closed"""
    with pytest.raises(ValidationError) as exc_info:
        ExecutorConfig.model_validate(tomllib.loads("""
default_permissions = "workspace"

[permissions.workspace.network.mitm.hooks.github_write]
host = "api.github.com"
methods = ["POST"]
path_prefixes = ["/repos/openai/"]
action = ["strip_auth"]

[permissions.workspace.network.mitm.actions.strip_auth]
"""))
    assert "network.mitm.actions.strip_auth must define at least one operation" in str(
        exc_info.value
    )


def test_permissions_profile_network_to_proxy_config_preserves_mitm_hooks():
    """对位 config_tests.rs:1584-1623"""
    network = NetworkToml(
        mode=PermissionNetworkMode.FULL,
        mitm=NetworkMitmToml(
            hooks={
                "github_write": NetworkMitmHookToml(
                    host="api.github.com",
                    methods=["POST"],
                    path_prefixes=["/repos/openai/"],
                    action=["strip_auth"],
                )
            },
            actions={
                "strip_auth": NetworkMitmActionToml(
                    strip_request_headers=["authorization"]
                )
            },
        ),
    )
    config = network.to_network_proxy_config()
    assert config.mode is PermissionNetworkMode.FULL
    assert config.mitm
    assert len(config.mitm_hooks) == 1
    assert config.mitm_hooks[0].host == "api.github.com"
    assert config.mitm_hooks[0].matcher.methods == ["POST"]
    assert config.mitm_hooks[0].actions.strip_request_headers == ["authorization"]


def test_permissions_profile_network_to_proxy_config_preserves_mitm_hook_declaration_order():
    """对位 config_tests.rs:1625-1666——IndexMap 声明序（Python dict 插入序）"""
    cfg = ExecutorConfig.model_validate(tomllib.loads("""
default_permissions = "workspace"

[permissions.workspace.network.mitm.actions.noop]
strip_request_headers = ["authorization"]

[permissions.workspace.network.mitm.hooks.z_first]
host = "api.github.com"
methods = ["POST"]
path_prefixes = ["/repos/openai/"]
action = ["noop"]

[permissions.workspace.network.mitm.hooks.a_second]
host = "api.github.com"
methods = ["POST"]
path_prefixes = ["/repos/"]
action = ["noop"]
"""))
    assert cfg.permissions is not None
    network = cfg.permissions.entries["workspace"].network
    assert network is not None
    config = network.to_network_proxy_config()
    assert len(config.mitm_hooks) == 2
    assert config.mitm_hooks[0].matcher.path_prefixes == ["/repos/openai/"]
    assert config.mitm_hooks[1].matcher.path_prefixes == ["/repos/"]


def test_permissions_profiles_require_default_permissions():
    """对位 config_tests.rs:3118-3161（对位点 mod.rs:3530-3541——
    有套餐表但未选 default_permissions 即拒；legacy 语法豁免不归本批）"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={":minimal": FilesystemPermissionToml.access(_READ)}
                )
            )
        }
    )
    with pytest.raises(ValueError) as exc_info:
        resolve_config_permission_selection(permissions, None)
    assert (
        str(exc_info.value)
        == "config defines `[permissions]` profiles but does not set "
        "`default_permissions`"
    )


def test_default_permissions_can_select_builtin_profile_without_permissions_table():
    """对位 config_tests.rs:3163-3203——无 [permissions] 表直选内置
    :workspace；条目断言：cwd 可写（钉物化）+ .git 元数据降只读条目在位"""
    selection = resolve_config_permission_selection(
        None, BUILT_IN_PERMISSION_PROFILE_WORKSPACE
    )
    assert selection.selected_profile_id == BUILT_IN_PERMISSION_PROFILE_WORKSPACE
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(None, ":workspace", context, None, [])
    entries = compiled.permission_profile.file_system.entries
    # 内置内容逐字对位（含 .git/.agents/.codex/.aws 符号降只读 skip 条目）
    assert entries[:8] == [
        _special_entry("root", _READ),
        _special_entry("project_roots", _WRITE),
        _special_entry("slash_tmp", _WRITE),
        _special_entry("tmpdir", _WRITE),
        *[
            _special_entry("project_roots", _READ, subpath=name, skip=True)
            for name in (".git", ".agents", ".codex", ".aws")
        ],
    ]
    # 钉 D-钉1：cwd 物化——项目根可写 + 元数据只读雕刻的具体条目在位
    assert _path_entry(str(cwd), _WRITE) in entries
    assert _path_entry(str(cwd.join(".git")), _READ, skip=True) in entries


def test_default_permissions_read_only_keeps_add_dir_read_only():
    """对位 config_tests.rs:3204-3239——:read-only 忽略 legacy 设置的附加
    可写根（builtin 只认 :workspace 的设置），活动身份 sidecar 对位"""
    extra_root = "/tmp/nova-golden-extra"
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(
        None,
        BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
        context,
        WorkspaceWriteSettings(writable_roots=(extra_root,)),
        [],
    )
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("root", _READ)
    ]
    assert compiled.workspace_roots == ()
    # 对位 ActivePermissionProfile::read_only()（models.rs:466-470）
    assert ActivePermissionProfile.read_only() == ActivePermissionProfile(
        id=BUILT_IN_PERMISSION_PROFILE_READ_ONLY
    )


def test_default_permissions_profile_populates_runtime_sandbox_policy():
    """对位 config_tests.rs:2424-2528——:minimal 读 + 工作区根写 + docs 读；
    条目清单按钉补齐 cwd 物化（对位 codex 运行期物化后的终态形状）"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":minimal": FilesystemPermissionToml.access(_READ),
                        ":workspace_roots": FilesystemPermissionToml.scoped(
                            {".": _WRITE, "docs": _READ}
                        ),
                    }
                )
            )
        }
    )
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(permissions, "dev", context, None, [])
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("minimal", _READ),
        _special_entry("project_roots", _WRITE),
        _special_entry("project_roots", _READ, subpath="docs"),
        _path_entry(str(cwd), _WRITE),
        _path_entry(str(cwd.join("docs")), _READ),
    ]
    assert compiled.permission_profile.network is NetworkSandboxPolicy.RESTRICTED
    # `.git` 无可写条目（对位 !can_write(.git) 断言的条目口径）
    assert not any(
        entry.path.type == "path"
        and entry.path.path == str(cwd.join(".git"))
        and entry.access is _WRITE
        for entry in compiled.permission_profile.file_system.entries
    )


def test_default_permissions_extended_profile_preserves_parent_metadata():
    """对位 config_tests.rs:2530-2584——resolved 保留选中套餐的 extends
    声明，ActivePermissionProfile sidecar 由其派生"""
    permissions = PermissionsToml(
        {
            "base": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={":minimal": FilesystemPermissionToml.access(_READ)}
                )
            ),
            "dev": PermissionProfileToml(extends="base"),
        }
    )
    resolved = resolve_permission_profile(permissions, "dev")
    assert resolved.extends == "base"
    assert ActivePermissionProfile(id="dev", extends=resolved.extends) == (
        ActivePermissionProfile(id="dev", extends="base")
    )
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(permissions, "dev", context, None, [])
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("minimal", _READ)
    ]


def test_default_permissions_profile_can_extend_builtin_workspace():
    """对位 config_tests.rs:3382-3476——继承 :workspace：项目根写与元数据
    雕刻保留，子套餐 :tmpdir 读覆盖父写，网络开启，sidecar 带父名"""
    permissions = PermissionsToml(
        {
            "workspace-with-network": PermissionProfileToml(
                extends=BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
                filesystem=FilesystemPermissionsToml(
                    entries={":tmpdir": FilesystemPermissionToml.access(_READ)}
                ),
                network=NetworkToml(enabled=True),
            )
        }
    )
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(
        permissions, "workspace-with-network", context, None, []
    )
    entries = compiled.permission_profile.file_system.entries
    # 继承自内置 :workspace 的 TOML 投影（skip 条目不投影——对位
    # permissions.rs:266-268）+ 子键覆盖
    assert entries[:4] == [
        _special_entry("root", _READ),
        _special_entry("slash_tmp", _WRITE),
        _special_entry("tmpdir", _READ),
        _special_entry("project_roots", _WRITE),
    ]
    assert not any(
        entry.path.value is not None
        and entry.path.value.kind == "tmpdir"
        and entry.access is _WRITE
        for entry in entries
    )
    assert _path_entry(str(cwd), _WRITE) in entries
    assert compiled.permission_profile.network is NetworkSandboxPolicy.ENABLED
    resolved = resolve_permission_profile(permissions, "workspace-with-network")
    assert ActivePermissionProfile(
        id="workspace-with-network", extends=resolved.extends
    ) == ActivePermissionProfile(
        id="workspace-with-network",
        extends=BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
    )


def test_default_permissions_profile_can_extend_builtin_read_only():
    """对位 config_tests.rs:3483-3546——继承 :read-only：保持只读、网络
    开启、sidecar 带父名"""
    permissions = PermissionsToml(
        {
            "read-only-with-network": PermissionProfileToml(
                extends=BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
                network=NetworkToml(enabled=True),
            )
        }
    )
    cwd = PermissionPathUri.parse("file:///Users/executor/project")
    context = PermissionPathContext(PermissionPathConvention.POSIX, cwd, None)
    compiled = compile_permission_profile(
        permissions, "read-only-with-network", context, None, []
    )
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("root", _READ)
    ]
    assert compiled.permission_profile.network is NetworkSandboxPolicy.ENABLED
    resolved = resolve_permission_profile(permissions, "read-only-with-network")
    assert resolved.extends == BUILT_IN_PERMISSION_PROFILE_READ_ONLY


def test_default_permissions_can_select_builtin_full_access_profile():
    """对位 config_tests.rs:3809-3831——:danger-full-access 物化为
    Disabled（显式脱沙箱）"""
    selection = resolve_config_permission_selection(
        None, BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS
    )
    assert (
        selection.selected_profile_id == BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    compiled = compile_permission_profile(
        None, BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS, context, None, []
    )
    assert compiled.permission_profile.type == "disabled"
    assert compiled.workspace_roots == ()


def test_legacy_danger_no_sandbox_is_rejected():
    """对位 config_tests.rs:3834-3857——legacy 全开放别名拒收"""
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    with pytest.raises(ValueError) as exc_info:
        compile_permission_profile(None, ":danger-no-sandbox", context, None, [])
    assert (
        str(exc_info.value) == "default_permissions refers to unknown built-in profile "
        "`:danger-no-sandbox`"
    )


def test_user_defined_permission_profile_names_cannot_use_builtin_prefix():
    """对位 config_tests.rs:3868-3899"""
    permissions = PermissionsToml({":custom": PermissionProfileToml()})
    with pytest.raises(ValueError) as exc_info:
        resolve_config_permission_selection(permissions, ":custom")
    assert (
        str(exc_info.value)
        == "permissions profile `:custom` uses a reserved built-in profile prefix"
    )


def test_unknown_builtin_permission_profile_name_is_rejected():
    """对位 config_tests.rs:3901-3926"""
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    with pytest.raises(ValueError) as exc_info:
        compile_permission_profile(None, ":unknown", context, None, [])
    assert (
        str(exc_info.value)
        == "default_permissions refers to unknown built-in profile `:unknown`"
    )


def test_permissions_profiles_allow_direct_write_roots_outside_workspace_root():
    """对位 config_tests.rs:3928-3993——工作区外的直接写根编译为具体写
    条目（codex 另断言 catalog 条目与 legacy 投影——目录面/兼容面不归
    本批，见文件头登记）"""
    external_write_path = "/tmp/nova-golden-external"
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                description="Workspace access.",
                filesystem=FilesystemPermissionsToml(
                    entries={
                        external_write_path: FilesystemPermissionToml.access(_WRITE)
                    }
                ),
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    compiled = compile_permission_profile(permissions, "dev", context, None, [])
    assert compiled.permission_profile.file_system.entries == [
        _path_entry("file:///tmp/nova-golden-external", _WRITE)
    ]


def test_permissions_profiles_reject_nested_entries_for_non_workspace_roots():
    """对位 config_tests.rs:3995-4041——非 :workspace_roots 符号根不收
    嵌套子路径"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":minimal": FilesystemPermissionToml.scoped({"docs": _READ})
                    }
                )
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    with pytest.raises(PermissionPathError) as exc_info:
        compile_permission_profile(permissions, "dev", context, None, [])
    assert (
        str(exc_info.value)
        == "filesystem path `:minimal` does not support nested entries"
    )


def test_permissions_profiles_allow_unknown_special_paths():
    """对位 config_tests.rs:4069-4113——未知 `:xxx` round-trip 成 Unknown
    条目（警告不拒，前向兼容纪律）"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":future_special_path": FilesystemPermissionToml.access(_READ)
                    }
                )
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    warnings: list[str] = []
    compiled = compile_permission_profile(permissions, "dev", context, None, warnings)
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("unknown", _READ, path=":future_special_path")
    ]
    assert any(
        "Configured filesystem path `:future_special_path` is not recognized "
        "by this version of Codex and will be ignored." in warning
        for warning in warnings
    )


def test_permissions_profiles_allow_unknown_special_paths_with_nested_entries():
    """对位 config_tests.rs:4115-4153——未知符号根可携带嵌套子路径"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":future_special_path": FilesystemPermissionToml.scoped(
                            {"docs": _READ}
                        )
                    }
                )
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    warnings: list[str] = []
    compiled = compile_permission_profile(permissions, "dev", context, None, warnings)
    assert compiled.permission_profile.file_system.entries == [
        _special_entry("unknown", _READ, path=":future_special_path", subpath="docs")
    ]
    assert any(
        "Configured filesystem path `:future_special_path` with nested entry "
        "`docs` is not recognized by this version of Codex and will be "
        "ignored." in warning
        for warning in warnings
    )


def test_permissions_profiles_allow_missing_filesystem_with_warning():
    """对位 config_tests.rs:4156-4186——无 filesystem 段 = 受限空策略 +
    启动警告"""
    permissions = PermissionsToml({"dev": PermissionProfileToml()})
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    warnings: list[str] = []
    compiled = compile_permission_profile(permissions, "dev", context, None, warnings)
    assert compiled.permission_profile.file_system.entries == []
    assert any(
        "Permissions profile `dev` does not define any recognized filesystem "
        "entries for this version of Codex." in warning
        for warning in warnings
    )


def test_permissions_profiles_allow_empty_filesystem_with_warning():
    """对位 config_tests.rs:4187-4213——空 filesystem 表同上"""
    permissions = PermissionsToml(
        {"dev": PermissionProfileToml(filesystem=FilesystemPermissionsToml())}
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    warnings: list[str] = []
    compiled = compile_permission_profile(permissions, "dev", context, None, warnings)
    assert compiled.permission_profile.file_system.entries == []
    assert any(
        "Permissions profile `dev` does not define any recognized filesystem "
        "entries for this version of Codex." in warning
        for warning in warnings
    )


def test_permissions_profiles_reject_workspace_root_parent_traversal():
    """对位 config_tests.rs:4215-4262——工作区根子路径拒绝 `..` 逃逸"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={
                        ":workspace_roots": FilesystemPermissionToml.scoped(
                            {"../sibling": _READ}
                        )
                    }
                )
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    with pytest.raises(PermissionPathError) as exc_info:
        compile_permission_profile(permissions, "dev", context, None, [])
    assert (
        str(exc_info.value)
        == "filesystem subpath `../sibling` must be a descendant path without "
        "`.` or `..` components"
    )


def test_permissions_profiles_allow_network_enablement():
    """对位 config_tests.rs:4264-4304——`network.enabled = true` 置沙箱
    网络位"""
    permissions = PermissionsToml(
        {
            "dev": PermissionProfileToml(
                filesystem=FilesystemPermissionsToml(
                    entries={":minimal": FilesystemPermissionToml.access(_READ)}
                ),
                network=NetworkToml(enabled=True),
            )
        }
    )
    context = PermissionPathContext(
        PermissionPathConvention.POSIX,
        PermissionPathUri.parse("file:///Users/executor/project"),
        None,
    )
    compiled = compile_permission_profile(permissions, "dev", context, None, [])
    assert compiled.permission_profile.network is NetworkSandboxPolicy.ENABLED


def test_permission_profile_falls_back_when_disallowed_by_requirements():
    """对位 mod.rs:4854-4878 `resolve_default_permissions` 的回落语义
    （config_tests.rs:11347 的 Config 装配用例核心——配置缺省被
    allowlist 否决 → 回落需求缺省 + 警告文案逐字）"""
    permissions = PermissionsToml({"dev": PermissionProfileToml()})
    requirements = PermissionRequirementsToml(
        allowed_permission_profiles={":read-only": True, ":workspace": True},
        default_permissions=None,
    )
    warnings: list[str] = []
    selection = resolve_config_permission_selection(
        permissions, "dev", requirements, startup_warnings=warnings
    )
    # 双内置皆放行 → 隐式缺省 :workspace（对位 mod.rs:4935-4944）
    assert selection.selected_profile_id == ":workspace"
    assert warnings == [
        "Configured value for `permission_profile` is disallowed by "
        "requirements; falling back from `dev` to required value `:workspace`."
    ]
