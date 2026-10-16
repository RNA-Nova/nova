"""exec 权限套餐族（A2b——对位 codex 锚点 39f4277087 的全量移植）。

模块边界对位：
- `path_uri.py` —— codex `utils/path-uri` + `config/src/path_context.rs`
  （纯计算路径件）；
- `toml.py` —— codex `config/src/permissions_toml.rs`（TOML 词汇 +
  extends 继承解析/环检测/合并）；
- `proxy_config.py` —— codex `network-proxy` 配置面（套餐网络编译落点）；
- `runtime.py` —— codex `protocol/src/models.rs` 的 sidecar 词汇（线上
  已有主形状不重复落地）；
- `compile.py` —— codex `core/src/config/permissions.rs` +
  `permission_path.rs` + protocol 物化件（内置三件套/解析/编译/物化）；
- `selection.py` —— codex `permission_profile_selection.rs` + mod.rs
  选择段。
"""

from .compile import (
    PROJECT_ROOTS_GLOB_PATTERN_PREFIX,
    CompiledPermissionProfile,
    WorkspaceWriteSettings,
    compile_permission_profile,
    is_builtin_permission_profile_name,
    network_proxy_config_from_profile_network,
    project_roots_glob_pattern,
    reject_unknown_builtin_permission_profile,
    resolve_permission_profile,
    validate_user_permission_profile_names,
)
from .path_uri import (
    PermissionPathContext,
    PermissionPathConvention,
    PermissionPathError,
    PermissionPathUri,
    normalize_windows_device_path,
)
from .proxy_config import (
    InjectedHeaderConfig,
    MitmHookActionsConfig,
    MitmHookBodyConfig,
    MitmHookConfig,
    MitmHookMatchConfig,
    PermissionNetworkMode,
    ProfileNetworkProxyConfig,
    ProxyNetworkDomainPermissionEntry,
    ProxyNetworkDomainPermissions,
    normalize_host,
)
from .runtime import (
    BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
    ActivePermissionProfile,
    AdditionalFileSystemPermissions,
    AdditionalNetworkPermissions,
    AdditionalPermissionProfile,
)
from .selection import (
    EffectivePermissionSelection,
    FilesystemRequirementsToml,
    PermissionRequirementsToml,
    PermissionsRequirementsToml,
    ResolvedPermissionProfileSelection,
    merge_managed_permission_profiles,
    resolve_config_permission_selection,
    resolve_default_permissions,
    resolve_effective_permission_selection,
    resolve_permission_profile_selection,
    validate_required_permission_profile_catalog,
)
from .toml import (
    FileSystemAccessModeToml,
    FilesystemPermissionsToml,
    FilesystemPermissionToml,
    NetworkDomainPermissionsToml,
    NetworkMitmActionToml,
    NetworkMitmHookToml,
    NetworkMitmInjectedHeaderToml,
    NetworkMitmToml,
    NetworkToml,
    NetworkUnixSocketPermissionsToml,
    PermissionProfileResolutionError,
    PermissionProfileResolutionErrorKind,
    PermissionProfileToml,
    PermissionsToml,
    WorkspaceRootsToml,
    merge_permission_profiles,
    overlay_network_domain_permissions,
)

__all__ = [
    # 路径件
    "PermissionPathContext",
    "PermissionPathConvention",
    "PermissionPathError",
    "PermissionPathUri",
    "normalize_windows_device_path",
    # TOML 词汇
    "PermissionsToml",
    "PermissionProfileToml",
    "WorkspaceRootsToml",
    "FilesystemPermissionsToml",
    "FilesystemPermissionToml",
    "FileSystemAccessModeToml",
    "NetworkToml",
    "NetworkDomainPermissionsToml",
    "NetworkUnixSocketPermissionsToml",
    "NetworkMitmToml",
    "NetworkMitmHookToml",
    "NetworkMitmActionToml",
    "NetworkMitmInjectedHeaderToml",
    # 继承解析
    "PermissionProfileResolutionError",
    "PermissionProfileResolutionErrorKind",
    "merge_permission_profiles",
    "overlay_network_domain_permissions",
    # 代理配置族
    "ProfileNetworkProxyConfig",
    "PermissionNetworkMode",
    "ProxyNetworkDomainPermissions",
    "ProxyNetworkDomainPermissionEntry",
    "MitmHookConfig",
    "MitmHookMatchConfig",
    "MitmHookActionsConfig",
    "MitmHookBodyConfig",
    "InjectedHeaderConfig",
    "normalize_host",
    # 运行时词汇
    "BUILT_IN_PERMISSION_PROFILE_READ_ONLY",
    "BUILT_IN_PERMISSION_PROFILE_WORKSPACE",
    "BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS",
    "ActivePermissionProfile",
    "AdditionalPermissionProfile",
    "AdditionalFileSystemPermissions",
    "AdditionalNetworkPermissions",
    # 编译
    "PROJECT_ROOTS_GLOB_PATTERN_PREFIX",
    "CompiledPermissionProfile",
    "WorkspaceWriteSettings",
    "compile_permission_profile",
    "is_builtin_permission_profile_name",
    "network_proxy_config_from_profile_network",
    "project_roots_glob_pattern",
    "reject_unknown_builtin_permission_profile",
    "resolve_permission_profile",
    "validate_user_permission_profile_names",
    # 选择
    "PermissionRequirementsToml",
    "PermissionsRequirementsToml",
    "FilesystemRequirementsToml",
    "EffectivePermissionSelection",
    "ResolvedPermissionProfileSelection",
    "merge_managed_permission_profiles",
    "resolve_config_permission_selection",
    "resolve_default_permissions",
    "resolve_effective_permission_selection",
    "resolve_permission_profile_selection",
    "validate_required_permission_profile_catalog",
]
