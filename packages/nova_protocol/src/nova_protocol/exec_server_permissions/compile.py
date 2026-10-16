"""权限套餐解析/编译管线（逐函数对位 codex 锚点 39f4277087
`core/src/config/permissions.rs` 879 行全文 + `permission_path.rs` 75 行
全文 + `protocol/src/permissions.rs` 物化件 + `protocol/src/permissions/
target.rs:122` 的 retain+add 物化变体）。

**退化语义钉（用户拍板）**：符号根 `:workspace_roots`/`:project_roots`
物化时的"会话根集合" = **{cwd} ∪ 配置显式根**（codex 两阶段——编译期
配置根 + 运行期会话根——我们尚无"中途批准加根"流，坍缩为编译期一次
物化；那是后续系统升级项）。物化保留符号条目并追加具体条目（对位
target.rs:122 `with_materialized_project_roots_for_path_uris`），服务端
经 policyContext 对符号根做最终解析，两通道结果一致。

编译产物即线上形状：`ExecPermissionProfile`（FileSystemSandboxContext 的
permissions 载荷）——服务端已理解（exec-server-protocol-core
models.rs:276-412 的 serde 形状对照）。

`tracing::warn!` 通道按枢纽纯洁性纪律不落——警告只进 `startup_warnings`
列表（对位 permissions.rs:846-849 的 push_warning 双通道之列表半）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..exec_server_wire import (
    ExecFileSystemPath,
    ExecFileSystemSandboxEntry,
    ExecFileSystemSpecialPath,
    ExecManagedFileSystemPermissions,
    ExecPermissionProfile,
    FileSystemAccessMode,
    FileSystemSandboxContext,
    NetworkSandboxPolicy,
    WireFileSystemPolicyContext,
)
from .path_uri import (
    PermissionPathContext,
    PermissionPathConvention,
    PermissionPathError,
    PermissionPathUri,
    normalize_windows_device_path,
)
from .proxy_config import ProfileNetworkProxyConfig
from .runtime import (
    BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
)
from .toml import (
    FilesystemPermissionsToml,
    FilesystemPermissionToml,
    NetworkToml,
    PermissionProfileToml,
    PermissionsToml,
)

#: 工作区根 glob 的符号前缀（对位 protocol/src/permissions.rs:600）
PROJECT_ROOTS_GLOB_PATTERN_PREFIX = "codex-project-roots://"


def _can_write(access: FileSystemAccessMode) -> bool:
    """对位 FileSystemAccessMode::can_write（protocol/src/permissions.rs:136-139
    ——线上枚举的方法面不进 wire 模块，编译管线就近持有同语义辅助）"""
    return access is FileSystemAccessMode.WRITE


#: 内置 :workspace 套餐的项目根元数据降只读名单（逐字对位
#: protocol/src/permissions.rs:40-53 的 PROTECTED_METADATA_* 常量族）
_PROTECTED_METADATA_SUBPATHS = (".git", ".agents", ".codex", ".aws")


def project_roots_glob_pattern(subpath: str) -> str:
    """对位 protocol/src/permissions.rs:602-605（`Path::display` 对本侧 str
    输入恒等——分隔符拼写原样保留）"""
    return f"{PROJECT_ROOTS_GLOB_PATTERN_PREFIX}{subpath}"


# =============================================================================
# 路径语法辅助（对位 permission_path.rs 全文）
# =============================================================================


def contains_glob_chars_for_platform(path: str, is_windows: bool) -> bool:
    """对位 permission_path.rs:63-71（Windows 先归一命名空间别名再扫描）"""
    if is_windows:
        normalized = normalize_windows_device_path(path)
        if normalized is not None:
            path = normalized
    return any(ch in "*?[]" for ch in path)


def _contains_glob(path: str, context: PermissionPathContext) -> bool:
    """对位 permission_path.rs:12-17"""
    return contains_glob_chars_for_platform(
        path, context.convention is PermissionPathConvention.WINDOWS
    )


def _absolute_path(path: str, context: PermissionPathContext) -> PermissionPathUri:
    """对位 permission_path.rs:19-34（拒绝相对路径——须绝对、`~/...` 或
    `:` 符号根）"""
    if (
        context.convention.home_relative_suffix(path) is None
        and PermissionPathUri.from_absolute_native_path(path, context.convention)
        is None
    ):
        raise PermissionPathError(
            f"filesystem path `{path}` must be absolute, use `~/...`, or "
            f"start with `:`"
        )
    return context.resolve_path(path)


def _relative_subpath(subpath: str, context: PermissionPathContext) -> str:
    """后代子路径校验（逐行对位 permission_path.rs:36-61——通过后保留
    原始拼写：内部 `.` 与重复分隔符在符号规则中原样保留）"""
    convention = context.convention
    components = convention.path_segments(subpath)
    first = components[0] if components else None
    has_drive_prefix = (
        convention is PermissionPathConvention.WINDOWS
        and len(subpath) >= 2
        and subpath[0].isascii()
        and subpath[0].isalpha()
        and subpath[1] == ":"
    )
    if (
        subpath != ""
        and first not in ("", ".", "..")
        and not has_drive_prefix
        and not any(component == ".." for component in components[1:])
    ):
        return subpath
    raise PermissionPathError(
        f"filesystem subpath `{subpath}` must be a descendant path without "
        f"`.` or `..` components"
    )


# =============================================================================
# 符号根族（对位 protocol/src/permissions.rs:141-180 FileSystemSpecialPath +
# permissions.rs:829-844 parse_special_path）
# =============================================================================


def parse_special_path(path: str) -> ExecFileSystemSpecialPath | None:
    """符号根解析（逐行对位 permissions.rs:829-844）。

    前向兼容纪律（对位 :824-828 的 WARNING 注释 + protocol
    permissions.rs:156-163）：未知 `:xxx` token round-trip 成 Unknown
    （警告不拒），新配置在旧运行时降级而非拒载。
    """
    if path == ":root":
        return ExecFileSystemSpecialPath(kind="root")
    if path == ":minimal":
        return ExecFileSystemSpecialPath(kind="minimal")
    # `:project_roots` 先于正名上线，保留为别名（对位 :833-835）
    if path in (":project_roots", ":workspace_roots"):
        return ExecFileSystemSpecialPath(kind="project_roots")
    if path == ":tmpdir":
        return ExecFileSystemSpecialPath(kind="tmpdir")
    if path == ":slash_tmp":
        return ExecFileSystemSpecialPath(kind="slash_tmp")
    if path.startswith(":"):
        return ExecFileSystemSpecialPath(kind="unknown", path=path)
    return None


def _is_project_roots(special: ExecFileSystemSpecialPath | None) -> bool:
    return special is not None and special.kind == "project_roots"


# =============================================================================
# 内置三件套（内容逐字对位 protocol/src/permissions.rs:610-620 read_only +
# 819-877 workspace_write + models.rs:411-418 名称常量）
# =============================================================================


def _special_entry(
    special: ExecFileSystemSpecialPath,
    access: FileSystemAccessMode,
    *,
    skip_missing: bool = False,
) -> ExecFileSystemSandboxEntry:
    return ExecFileSystemSandboxEntry(
        path=ExecFileSystemPath(type="special", value=special),
        access=access,
        missing_path_behavior="skip" if skip_missing else None,
    )


def _read_only_file_system_entries() -> list[ExecFileSystemSandboxEntry]:
    """对位 permissions.rs:610-620——全盘可读"""
    return [
        _special_entry(
            ExecFileSystemSpecialPath(kind="root"), FileSystemAccessMode.READ
        )
    ]


def _workspace_write_file_system_entries(
    exclude_tmpdir_env_var: bool, exclude_slash_tmp: bool
) -> list[ExecFileSystemSandboxEntry]:
    """内置 `:workspace` 套餐内容（逐行对位
    protocol/src/permissions.rs:819-877 `workspace_write`）。

    codex 签名带 `writable_roots` 参数，但编译路径恒传空（对位
    permissions.rs:96-113——legacy 设置经 WorkspaceWriteSettings 成为
    物化根集合而非条目）；legacy SandboxPolicy 转换面不随本批移植。
    """
    entries = [
        _special_entry(
            ExecFileSystemSpecialPath(kind="root"), FileSystemAccessMode.READ
        ),
        _special_entry(
            ExecFileSystemSpecialPath(kind="project_roots"),
            FileSystemAccessMode.WRITE,
        ),
    ]
    if not exclude_slash_tmp:
        entries.append(
            _special_entry(
                ExecFileSystemSpecialPath(kind="slash_tmp"),
                FileSystemAccessMode.WRITE,
            )
        )
    if not exclude_tmpdir_env_var:
        entries.append(
            _special_entry(
                ExecFileSystemSpecialPath(kind="tmpdir"), FileSystemAccessMode.WRITE
            )
        )
    # 元数据降只读（对位 :858-861 的四连 append_default_read_only_
    # project_root_subpath_if_no_explicit_rule——初构无既有条目可撞，
    # skip_missing_path 行为对位 :2513-2516）
    entries.extend(
        _special_entry(
            ExecFileSystemSpecialPath(kind="project_roots", subpath=name),
            FileSystemAccessMode.READ,
            skip_missing=True,
        )
        for name in _PROTECTED_METADATA_SUBPATHS
    )
    return entries


@dataclass(frozen=True)
class WorkspaceWriteSettings:
    """legacy workspace-write 设置（逐字段对位 permissions.rs:375-381）。

    `writable_roots` 为原生绝对路径或 `file://` URI 文本（codex 侧为已解析
    PathUri——本侧在编译点经上下文解析，解析语义不变）。
    """

    writable_roots: tuple[str, ...] = ()
    network_access: bool = False
    exclude_tmpdir_env_var: bool = False
    exclude_slash_tmp: bool = False


def is_builtin_permission_profile_name(profile_name: str) -> bool:
    """对位 permissions.rs:81-88"""
    return profile_name in (
        BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
        BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
        BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS,
    )


def _builtin_permission_profile(
    profile_name: str, workspace_write: WorkspaceWriteSettings | None
) -> ExecPermissionProfile | None:
    """内置套餐内容（逐行对位 permissions.rs:90-117）"""
    if profile_name == BUILT_IN_PERMISSION_PROFILE_READ_ONLY:
        return ExecPermissionProfile(
            type="managed",
            file_system=ExecManagedFileSystemPermissions(
                type="restricted", entries=_read_only_file_system_entries()
            ),
            network=NetworkSandboxPolicy.RESTRICTED,
        )
    if profile_name == BUILT_IN_PERMISSION_PROFILE_WORKSPACE:
        if workspace_write is not None:
            return ExecPermissionProfile(
                type="managed",
                file_system=ExecManagedFileSystemPermissions(
                    type="restricted",
                    entries=_workspace_write_file_system_entries(
                        workspace_write.exclude_tmpdir_env_var,
                        workspace_write.exclude_slash_tmp,
                    ),
                ),
                network=(
                    NetworkSandboxPolicy.ENABLED
                    if workspace_write.network_access
                    else NetworkSandboxPolicy.RESTRICTED
                ),
            )
        return ExecPermissionProfile(
            type="managed",
            file_system=ExecManagedFileSystemPermissions(
                type="restricted",
                entries=_workspace_write_file_system_entries(False, False),
            ),
            network=NetworkSandboxPolicy.RESTRICTED,
        )
    if profile_name == BUILT_IN_PERMISSION_PROFILE_DANGER_FULL_ACCESS:
        return ExecPermissionProfile(type="disabled")
    return None


def validate_user_permission_profile_names(
    permissions: PermissionsToml | None,
) -> None:
    """用户套餐名不得占用内置 `:` 前缀（对位 permissions.rs:119-138）"""
    if permissions is None:
        return
    for profile_name in permissions.entries:
        if profile_name.startswith(":"):
            raise ValueError(
                f"permissions profile `{profile_name}` uses a reserved "
                f"built-in profile prefix"
            )


def reject_unknown_builtin_permission_profile(profile_name: str) -> None:
    """对位 permissions.rs:517-526"""
    if profile_name.startswith(":"):
        raise ValueError(
            f"default_permissions refers to unknown built-in profile "
            f"`{profile_name}`"
        )


# =============================================================================
# 内置套餐 → TOML 投影（extends 父解析用——对位 permissions.rs:230-351）
# =============================================================================


def _insert_filesystem_permission_toml(
    entries: dict[str, FilesystemPermissionToml],
    entry: ExecFileSystemSandboxEntry,
) -> None:
    """对位 permissions.rs:262-284（skip_missing_path 条目不投影——
    对位 :266-268）"""
    if entry.missing_path_behavior == "skip":
        return
    path = entry.path
    if path.type == "path":
        assert path.path is not None
        native = PermissionPathUri.parse(path.path).inferred_native_path_string()
        entries[native] = FilesystemPermissionToml.access(entry.access)
    elif path.type == "glob_pattern":
        assert path.pattern is not None
        entries[path.pattern] = FilesystemPermissionToml.access(entry.access)
    else:
        assert path.value is not None
        _insert_special_filesystem_permission_toml(entries, path.value, entry.access)


def _insert_special_filesystem_permission_toml(
    entries: dict[str, FilesystemPermissionToml],
    value: ExecFileSystemSpecialPath,
    access: FileSystemAccessMode,
) -> None:
    """对位 permissions.rs:286-332"""
    if value.kind == "root":
        entries[":root"] = FilesystemPermissionToml.access(access)
    elif value.kind == "minimal":
        entries[":minimal"] = FilesystemPermissionToml.access(access)
    elif value.kind == "project_roots":
        _insert_scoped_filesystem_permission_toml(
            entries, ":workspace_roots", value.subpath or ".", access
        )
    elif value.kind == "tmpdir":
        entries[":tmpdir"] = FilesystemPermissionToml.access(access)
    elif value.kind == "slash_tmp":
        entries[":slash_tmp"] = FilesystemPermissionToml.access(access)
    elif value.kind == "unknown":
        assert value.path is not None
        if value.subpath is not None:
            _insert_scoped_filesystem_permission_toml(
                entries, value.path, value.subpath, access
            )
        else:
            entries[value.path] = FilesystemPermissionToml.access(access)


def _insert_scoped_filesystem_permission_toml(
    entries: dict[str, FilesystemPermissionToml],
    path: str,
    subpath: str,
    access: FileSystemAccessMode,
) -> None:
    """对位 permissions.rs:334-351（既有 Access 条目被子路径表取代）"""
    existing = entries.get(path)
    if existing is not None and not existing.is_access:
        existing.scoped_value[subpath] = access
        return
    entries[path] = FilesystemPermissionToml.scoped({subpath: access})


def _extensible_builtin_parent_profile(
    profile_name: str,
) -> PermissionProfileToml | None:
    """可作 extends 父的内置套餐 TOML 投影（对位
    permissions.rs:230-241）——`:danger-full-access` 不可作父（None →
    resolve 侧 UnsupportedBuiltInParent 报错）"""
    if profile_name == BUILT_IN_PERMISSION_PROFILE_READ_ONLY:
        entries = _read_only_file_system_entries()
    elif profile_name == BUILT_IN_PERMISSION_PROFILE_WORKSPACE:
        entries = _workspace_write_file_system_entries(False, False)
    else:
        return None
    # 对位 permission_profile_toml_from_file_system_policy（:243-260）
    filesystem_entries: dict[str, FilesystemPermissionToml] = {}
    for entry in entries:
        _insert_filesystem_permission_toml(filesystem_entries, entry)
    return PermissionProfileToml(
        filesystem=FilesystemPermissionsToml(entries=filesystem_entries)
    )


# =============================================================================
# 选择解析（对位 permissions.rs:221-228）
# =============================================================================


def resolve_permission_profile(
    permissions: PermissionsToml, profile_name: str
) -> PermissionProfileToml:
    """解析命名套餐及其继承配置（内置父套餐经
    `_extensible_builtin_parent_profile` 兜底）"""
    return permissions.resolve_profile(profile_name, _extensible_builtin_parent_profile)


def network_proxy_config_from_profile_network(
    network: NetworkToml | None,
) -> ProfileNetworkProxyConfig:
    """套餐 network → 代理配置（对位 permissions.rs:140-152）。

    套餐的 `network.enabled` 控制**沙箱网络**开关；套餐可提供代理设置供
    特性门消费，但不会自行启动托管代理——`enabled` 恒强制 False。
    """
    config = (
        network.to_network_proxy_config()
        if network is not None
        else ProfileNetworkProxyConfig()
    )
    config.enabled = False
    return config


# =============================================================================
# 物化（对位 protocol/src/permissions.rs:1252-1330 + target.rs:117-129）
# =============================================================================


def _resolve_scoped_workspace_path(
    root: PermissionPathUri, subpath: str
) -> PermissionPathUri | None:
    """对位 protocol/src/permissions.rs:2116-2132——拒绝绝对化/`.`/`..`/
    Windows 冒号段，拼接后不得越出根"""
    convention = root.infer_path_convention()
    if convention is None:
        return None
    is_windows = convention is PermissionPathConvention.WINDOWS
    segments = convention.path_segments(subpath)
    if (
        subpath.startswith("/")
        or (is_windows and subpath.startswith("\\"))
        or any(segment in (".", "..") for segment in segments)
        or (is_windows and any(":" in segment for segment in segments))
    ):
        return None
    try:
        path = root.join(subpath)
    except PermissionPathError:
        return None
    return path if path.starts_with(root) else None


def _try_materialize_project_roots(
    entries: list[ExecFileSystemSandboxEntry],
    workspace_roots: list[PermissionPathUri],
) -> list[ExecFileSystemSandboxEntry] | None:
    """替换式物化（逐行对位
    protocol/src/permissions.rs:1257-1330
    `try_materialize_project_roots_with_path_uris`）。

    返回 None = 物化不安全（legacy home 相对 workspace 拒绝在缺少 home
    事实时无法安全收窄——对位 :1289-1293 的注释），调用侧 fail-closed。
    """
    materialized: list[ExecFileSystemSandboxEntry] = []
    for entry in entries:
        subpath: str | None = None
        is_glob = False
        path = entry.path
        if (
            path.type == "special"
            and path.value is not None
            and path.value.kind == "project_roots"
        ):
            subpath = path.value.subpath
        elif (
            path.type == "glob_pattern"
            and path.pattern is not None
            and path.pattern.startswith(PROJECT_ROOTS_GLOB_PATTERN_PREFIX)
        ):
            subpath = path.pattern[len(PROJECT_ROOTS_GLOB_PATTERN_PREFIX) :]
            is_glob = True
        else:
            materialized.append(entry)
            continue
        if (
            entry.access is FileSystemAccessMode.DENY
            and subpath is not None
            and any(
                (convention := root.infer_path_convention()) is not None
                and convention.home_relative_suffix(subpath) is not None
                for root in workspace_roots
            )
        ):
            # 旧策略可能把这些规则扩展到工作区之外；缺少 home 事实时只拒
            # 工作区会弱化它们（对位 :1289-1293）
            return None
        for root in workspace_roots:
            if is_glob and (
                root.infer_path_convention() is None
                or _validate_glob_directory_safe(root) is False
            ):
                # 不可失败物化器不得产出不安全 glob——复用 deny-root 兜底
                # （对位 :1295-1301）
                resolved = None
            elif subpath is None:
                resolved = root
            else:
                resolved = _resolve_scoped_workspace_path(root, subpath)
            if resolved is not None and is_glob:
                new_path = ExecFileSystemPath(
                    type="glob_pattern",
                    pattern=resolved.inferred_native_path_string(),
                )
                new_access = entry.access
            elif resolved is not None:
                new_path = ExecFileSystemPath(
                    type="path", path=resolved.to_uri_string()
                )
                new_access = entry.access
            elif not _can_write(entry.access):
                new_path = ExecFileSystemPath(type="path", path=root.to_uri_string())
                new_access = FileSystemAccessMode.DENY
            else:
                continue
            materialized.append(
                ExecFileSystemSandboxEntry(
                    path=new_path,
                    access=new_access,
                    missing_path_behavior=entry.missing_path_behavior,
                )
            )
    return materialized


def _validate_glob_directory_safe(root: PermissionPathUri) -> bool:
    """`validate_glob_directory` 的布尔化（对位 :1296-1299 的 is_err 判定）"""
    convention = root.infer_path_convention()
    if convention is None:
        return False
    try:
        root.validate_glob_directory(convention)
    except PermissionPathError:
        return False
    return True


def _with_materialized_project_roots(
    entries: list[ExecFileSystemSandboxEntry],
    workspace_roots: list[PermissionPathUri],
) -> list[ExecFileSystemSandboxEntry]:
    """retain+add 物化（对位 target.rs:117-129——保留符号条目，追加各根
    的具体条目；物化失败 fail-closed 为空 restricted）"""
    materialized = _try_materialize_project_roots(entries, workspace_roots)
    if materialized is None:
        return []
    result = list(entries)
    for entry in materialized:
        if entry not in result:
            result.append(entry)
    return result


# =============================================================================
# 编译（对位 permissions.rs:393-761）
# =============================================================================


@dataclass(frozen=True)
class CompiledPermissionProfile:
    """编译后的套餐（对位 permissions.rs:383-388——配置根已物化，符号根
    保留供运行时/服务端按 policyContext 最终解析）"""

    permission_profile: ExecPermissionProfile
    #: 套餐声明的工作区根（**不含** cwd——cwd 由物化根集合/policyContext
    #: 承载，对位 codex CompiledPermissionProfile.workspace_roots 语义）
    workspace_roots: tuple[PermissionPathUri, ...] = field(default_factory=tuple)

    def to_sandbox_context(self, cwd: str | None = None) -> FileSystemSandboxContext:
        """编译产物 → 线上沙箱上下文（物化目标形状钉）。

        `policyContext` = 会话根集合 {cwd} ∪ 套餐声明根（去重、cwd 在前），
        与编译期物化根集合一致——服务端符号根解析与编译产物保持同集合。
        cwd 归一复用线上 `_policy_context`（file:// URI 形态）。
        """
        policy_context: WireFileSystemPolicyContext | None = None
        if cwd:
            base = FileSystemSandboxContext._policy_context(cwd)
            assert base is not None
            roots = [base.cwd, *(root.to_uri_string() for root in self.workspace_roots)]
            deduped = list(dict.fromkeys(root for root in roots if root))
            policy_context = WireFileSystemPolicyContext(
                cwd=base.cwd, workspaceRoots=deduped
            )
        # 注：`policyContext` 带线上 alias——绕开构造参数按属性赋值
        # （pyright 对 populate_by_name 模型只认 alias 参数名；wire 模块
        # 既有工厂的同型报错为 HEAD 存量）
        context_model = FileSystemSandboxContext(permissions=self.permission_profile)
        context_model.policy_context = policy_context
        return context_model


def compile_permission_profile(
    permissions: PermissionsToml | None,
    profile_name: str,
    context: PermissionPathContext,
    workspace_write: WorkspaceWriteSettings | None = None,
    startup_warnings: list[str] | None = None,
    *,
    emit_glob_platform_warnings: bool = False,
) -> CompiledPermissionProfile:
    """解析选中套餐并物化（逐行对位 permissions.rs:393-451）。

    `emit_glob_platform_warnings` 对位 codex 的 `cfg!(not(target_os =
    "macos"))` 编译期门（:468）：非 macOS 沙箱平台的 glob 支持警告——本侧
    纯计算不读宿主平台，由调用方按 **executor 平台**传入（macOS  executor
    传 False）。
    """
    warnings = startup_warnings if startup_warnings is not None else []
    builtin = _builtin_permission_profile(profile_name, workspace_write)
    if builtin is not None:
        entries = list(builtin.file_system.entries)
        glob_scan_max_depth = builtin.file_system.glob_scan_max_depth
        network = builtin.network
        builtin_workspace_roots: list[PermissionPathUri] = []
        if profile_name == BUILT_IN_PERMISSION_PROFILE_WORKSPACE:
            for root in (workspace_write.writable_roots if workspace_write else ()):
                builtin_workspace_roots.append(
                    _resolve_workspace_write_root(root, context)
                )
        workspace_roots = builtin_workspace_roots
    else:
        reject_unknown_builtin_permission_profile(profile_name)
        if permissions is None:
            raise ValueError("default_permissions requires a `[permissions]` table")
        profile = resolve_permission_profile(permissions, profile_name)
        entries, glob_scan_max_depth, network = _compile_resolved_permission_profile(
            profile, profile_name, context, warnings, emit_glob_platform_warnings
        )
        workspace_roots = (
            [
                context.resolve_path(path)
                for path in profile.workspace_roots.enabled_roots()
            ]
            if profile.workspace_roots is not None
            else []
        )
    # 去重保序（对位 :440-441 的 HashSet retain）
    workspace_roots = list(dict.fromkeys(workspace_roots))
    # 退化语义钉：物化根集合 = {cwd} ∪ 配置显式根
    session_roots = list(
        dict.fromkeys(
            ([context.base_dir] if context.base_dir is not None else [])
            + workspace_roots
        )
    )
    entries = _with_materialized_project_roots(entries, session_roots)
    if builtin is not None and builtin.type == "disabled":
        permission_profile = builtin
    else:
        # 对位 PermissionProfile::from_runtime_permissions（models.rs:602-620）
        # ——编译路径恒产 Restricted Managed
        permission_profile = ExecPermissionProfile(
            type="managed",
            file_system=ExecManagedFileSystemPermissions(
                type="restricted",
                entries=entries,
                glob_scan_max_depth=glob_scan_max_depth,
            ),
            network=network,
        )
    return CompiledPermissionProfile(
        permission_profile=permission_profile,
        workspace_roots=tuple(workspace_roots),
    )


def _resolve_workspace_write_root(
    root: str, context: PermissionPathContext
) -> PermissionPathUri:
    """legacy 设置根解析（对位 permissions.rs:407-412 的
    `context.resolve_against(".", root)`——URI 规范化用途）"""
    uri = (
        PermissionPathUri.parse(root)
        if root.startswith("file://")
        else PermissionPathUri.from_absolute_native_path(root, context.convention)
    )
    if uri is None:
        raise PermissionPathError(
            f"path `{root}` is not absolute using {context.convention} path syntax"
        )
    return context.resolve_against(".", uri)


def _compile_resolved_permission_profile(
    profile: PermissionProfileToml,
    profile_name: str,
    context: PermissionPathContext,
    startup_warnings: list[str],
    emit_glob_platform_warnings: bool,
) -> tuple[list[ExecFileSystemSandboxEntry], int | None, NetworkSandboxPolicy]:
    """对位 permissions.rs:453-515"""
    entries: list[ExecFileSystemSandboxEntry] = []
    base_network_sandbox_policy = NetworkSandboxPolicy.RESTRICTED
    filesystem = profile.filesystem
    if filesystem is not None:
        if filesystem.is_empty():
            _push_warning(
                startup_warnings, _missing_filesystem_entries_warning(profile_name)
            )
        else:
            if emit_glob_platform_warnings:
                for pattern in _unsupported_read_write_glob_paths(filesystem, context):
                    _push_warning(
                        startup_warnings,
                        f"Filesystem glob `{pattern}` uses `read` or `write` "
                        "access, which is not fully supported by this "
                        "platform's sandboxing. Use an exact path or trailing "
                        "`/**` subtree rule instead. `deny` globs are "
                        "supported.",
                    )
                for pattern in _unbounded_unreadable_globstar_paths(filesystem):
                    _push_warning(
                        startup_warnings,
                        f"Filesystem deny-read glob `{pattern}` uses `**`. "
                        "Non-macOS sandboxing does not support unbounded `**` "
                        "natively; set `glob_scan_max_depth` in this "
                        "filesystem profile to cap Linux glob expansion and "
                        "silence this warning, or enumerate explicit depths "
                        "such as `*.env`, `*/*.env`, and `*/*/*.env`.",
                    )
            # BTreeMap 键序（对位 :230 的 BTreeMap 条目表）
            for path in sorted(filesystem.entries):
                entries.extend(
                    _compile_filesystem_permission(
                        path,
                        filesystem.entries[path],
                        context,
                        startup_warnings,
                    )
                )
    else:
        _push_warning(
            startup_warnings, _missing_filesystem_entries_warning(profile_name)
        )
    glob_scan_max_depth = _validate_glob_scan_max_depth(
        filesystem.glob_scan_max_depth if filesystem is not None else None
    )
    network_sandbox_policy = _compile_network_sandbox_policy(
        profile.network, base_network_sandbox_policy
    )
    return entries, glob_scan_max_depth, network_sandbox_policy


def _compile_network_sandbox_policy(
    network: NetworkToml | None,
    base_network_sandbox_policy: NetworkSandboxPolicy,
) -> NetworkSandboxPolicy:
    """对位 permissions.rs:528-541——`network.enabled` 控制沙箱网络位"""
    if network is None:
        return base_network_sandbox_policy
    if network.enabled is True:
        return NetworkSandboxPolicy.ENABLED
    if network.enabled is False:
        return NetworkSandboxPolicy.RESTRICTED
    return base_network_sandbox_policy


def _compile_filesystem_permission(
    path: str,
    permission: FilesystemPermissionToml,
    context: PermissionPathContext,
    startup_warnings: list[str],
) -> list[ExecFileSystemSandboxEntry]:
    """对位 permissions.rs:543-614"""
    entries: list[ExecFileSystemSandboxEntry] = []
    if permission.is_access:
        access = permission.access_value
        entries.append(
            ExecFileSystemSandboxEntry(
                path=_compile_filesystem_access_path(
                    path, access, context, startup_warnings
                ),
                access=access,
            )
        )
        return entries
    for subpath in sorted(permission.scoped_value):
        access = permission.scoped_value[subpath]
        if _is_project_roots(parse_special_path(path)) and (
            context.convention.home_relative_suffix(subpath) is not None
        ):
            # 工作区根作用域下的 home 相对子路径逃逸作用域、直接对 home
            # 解析（对位 :560-577）
            _relative_subpath(subpath, context)
            entries.append(
                ExecFileSystemSandboxEntry(
                    path=_compile_filesystem_access_path(
                        subpath, access, context, startup_warnings
                    ),
                    access=access,
                )
            )
            continue
        has_glob = _contains_glob(subpath, context)
        special = parse_special_path(path)
        can_compile_as_pattern = special is None or _is_project_roots(special)
        if has_glob and access is FileSystemAccessMode.DENY and can_compile_as_pattern:
            # 作用域 glob 语法是一等策略模式条目；字面子路径仍走精确路径
            # 解析器（对位 :583-597 的注释纪律）
            entries.append(
                ExecFileSystemSandboxEntry(
                    path=ExecFileSystemPath(
                        type="glob_pattern",
                        pattern=_compile_scoped_filesystem_pattern(
                            path, subpath, access, context
                        ),
                    ),
                    access=access,
                )
            )
        else:
            compiled_subpath = _compile_read_write_glob_path(subpath, access, context)
            entries.append(
                ExecFileSystemSandboxEntry(
                    path=_compile_scoped_filesystem_path(
                        path, compiled_subpath, context, startup_warnings
                    ),
                    access=access,
                )
            )
    return entries


def _compile_filesystem_access_path(
    path: str,
    access: FileSystemAccessMode,
    context: PermissionPathContext,
    startup_warnings: list[str],
) -> ExecFileSystemPath:
    """对位 permissions.rs:616-640"""
    if not _contains_glob(path, context):
        return _compile_filesystem_path(path, context, startup_warnings)
    if access is FileSystemAccessMode.DENY:
        # 顶层 glob deny 条目先过绝对路径解析器再成策略模式（对位
        # :626-636 的注释）
        resolved = _absolute_path(path, context)
        return ExecFileSystemPath(
            type="glob_pattern",
            pattern=resolved.to_config_path_string(context.convention),
        )
    stripped = _compile_read_write_glob_path(path, access, context)
    return _compile_filesystem_path(stripped, context, startup_warnings)


def _compile_filesystem_path(
    path: str,
    context: PermissionPathContext,
    startup_warnings: list[str],
) -> ExecFileSystemPath:
    """对位 permissions.rs:642-653"""
    special = parse_special_path(path)
    if special is not None:
        _maybe_push_unknown_special_path_warning(special, startup_warnings)
        return ExecFileSystemPath(type="special", value=special)
    return ExecFileSystemPath(
        type="path", path=_absolute_path(path, context).to_uri_string()
    )


def _compile_scoped_filesystem_path(
    path: str,
    subpath: str,
    context: PermissionPathContext,
    startup_warnings: list[str],
) -> ExecFileSystemPath:
    """对位 permissions.rs:655-691"""
    if subpath == ".":
        return _compile_filesystem_path(path, context, startup_warnings)
    special = parse_special_path(path)
    if special is not None:
        checked_subpath = _relative_subpath(subpath, context)
        if _is_project_roots(special):
            compiled = ExecFileSystemSpecialPath(
                kind="project_roots", subpath=checked_subpath
            )
        elif special.kind == "unknown":
            assert special.path is not None
            compiled = ExecFileSystemSpecialPath(
                kind="unknown", path=special.path, subpath=checked_subpath
            )
        else:
            raise PermissionPathError(
                f"filesystem path `{path}` does not support nested entries"
            )
        _maybe_push_unknown_special_path_warning(compiled, startup_warnings)
        return ExecFileSystemPath(type="special", value=compiled)
    checked_subpath = _relative_subpath(subpath, context)
    base = _absolute_path(path, context)
    return ExecFileSystemPath(
        type="path",
        path=context.resolve_against(checked_subpath, base).to_uri_string(),
    )


def _compile_scoped_filesystem_pattern(
    path: str,
    subpath: str,
    access: FileSystemAccessMode,
    context: PermissionPathContext,
) -> str:
    """对位 permissions.rs:693-729。

    模式条目当前只表达 deny-read（对位 :699-701 的注释纪律——更宽的访问
    模式意味着沙箱策略尚不表达的 glob 读/写放行语义）。
    """
    if access is not FileSystemAccessMode.DENY:
        raise PermissionPathError(
            f"filesystem glob subpath `{subpath}` only supports `deny` access"
        )
    checked_subpath = _relative_subpath(subpath, context)
    special = parse_special_path(path)
    if _is_project_roots(special):
        # `:workspace_roots` glob 保持符号化直至工作区根已知（对位
        # :711-715 的注释）
        return project_roots_glob_pattern(checked_subpath)
    if special is not None:
        raise PermissionPathError(
            f"filesystem path `{path}` does not support nested entries"
        )
    base = _absolute_path(path, context)
    return context.resolve_against(checked_subpath, base).to_config_path_string(
        context.convention
    )


def _compile_read_write_glob_path(
    path: str, access: FileSystemAccessMode, context: PermissionPathContext
) -> str:
    """read/write 的 glob 只放行尾部 `/**` 子树形式（逐行对位
    permissions.rs:731-751）"""
    if not _contains_glob(path, context):
        return path
    path_without_trailing_glob = _remove_trailing_glob_suffix(path)
    if not _contains_glob(path_without_trailing_glob, context):
        return path_without_trailing_glob
    raise PermissionPathError(
        f"filesystem glob path `{path}` only supports `deny` access; use an "
        f"exact path or trailing `/**` for `{access.value}` subtree access"
    )


def _unsupported_read_write_glob_paths(
    filesystem: FilesystemPermissionsToml, context: PermissionPathContext
) -> list[str]:
    """对位 permissions.rs:753-782（deny glob 是受支持的 deny-read 规则，
    只 read/write glob 告警）"""
    patterns: list[str] = []
    for path in sorted(filesystem.entries):
        permission = filesystem.entries[path]
        if permission.is_access:
            if permission.access_value is not FileSystemAccessMode.DENY and (
                _contains_glob(_remove_trailing_glob_suffix(path), context)
            ):
                patterns.append(path)
        else:
            for subpath in sorted(permission.scoped_value):
                access = permission.scoped_value[subpath]
                if access is not FileSystemAccessMode.DENY and _contains_glob(
                    _remove_trailing_glob_suffix(subpath), context
                ):
                    patterns.append(f"{path}/{subpath}")
    return patterns


def _unbounded_unreadable_globstar_paths(
    filesystem: FilesystemPermissionsToml,
) -> list[str]:
    """对位 permissions.rs:784-808（已配置 glob_scan_max_depth 时静默）"""
    if filesystem.glob_scan_max_depth is not None:
        return []
    patterns: list[str] = []
    for path in sorted(filesystem.entries):
        permission = filesystem.entries[path]
        if permission.is_access:
            if permission.access_value is FileSystemAccessMode.DENY and "**" in path:
                patterns.append(path)
        else:
            for subpath in sorted(permission.scoped_value):
                if (
                    permission.scoped_value[subpath] is FileSystemAccessMode.DENY
                    and "**" in subpath
                ):
                    patterns.append(f"{path}/{subpath}")
    return patterns


def _validate_glob_scan_max_depth(max_depth: int | None) -> int | None:
    """对位 permissions.rs:810-818"""
    if max_depth == 0:
        raise ValueError("glob_scan_max_depth must be at least 1")
    return max_depth


def _remove_trailing_glob_suffix(path: str) -> str:
    """对位 permissions.rs:820-822"""
    return path[: -len("/**")] if path.endswith("/**") else path


# =============================================================================
# 警告件（对位 permissions.rs:846-875）
# =============================================================================


def _push_warning(startup_warnings: list[str], message: str) -> None:
    """对位 push_warning（:846-849）——tracing 半通道按枢纽纪律不落"""
    startup_warnings.append(message)


def _missing_filesystem_entries_warning(profile_name: str) -> str:
    """逐字对位 :851-855"""
    return (
        f"Permissions profile `{profile_name}` does not define any recognized "
        "filesystem entries for this version of Codex. Filesystem access will "
        "remain restricted. Upgrade Codex if this profile expects filesystem "
        "permissions."
    )


def _maybe_push_unknown_special_path_warning(
    special: ExecFileSystemSpecialPath, startup_warnings: list[str]
) -> None:
    """逐字对位 :857-875"""
    if special.kind != "unknown" or special.path is None:
        return
    if special.subpath is not None:
        _push_warning(
            startup_warnings,
            f"Configured filesystem path `{special.path}` with nested entry "
            f"`{special.subpath}` is not recognized by this version of Codex "
            "and will be ignored. Upgrade Codex if this path is required.",
        )
    else:
        _push_warning(
            startup_warnings,
            f"Configured filesystem path `{special.path}` is not recognized "
            "by this version of Codex and will be ignored. Upgrade Codex if "
            "this path is required.",
        )
