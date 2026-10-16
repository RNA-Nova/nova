"""`[permissions]` TOML 词汇 + extends 继承解析（逐字段对位 codex
`config/src/permissions_toml.rs`，锚点 39f4277087）。

形状纪律：
- 本模块是**配置侧**词汇（snake_case 键、TOML 语义），不经 dump_wire；
- serde `flatten` 容器（`PermissionsToml`/`WorkspaceRootsToml`/
  `NetworkDomainPermissionsToml`/`NetworkUnixSocketPermissionsToml`）落
  Pydantic RootModel；`FilesystemPermissionsToml` 的 flatten 条目表经
  before-validator 切分（`glob_scan_max_depth` 为唯一具名字段，其余键皆为
  条目——对位 :223-231）；
- Rust `BTreeMap` 的键序语义 = 本侧迭代点显式 `sorted()`（编译/枚举顺序与
  codex 一致）；`IndexMap`（mitm hooks/actions）= Python dict 天然插入序。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Callable, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    RootModel,
    model_validator,
)

from ..exec_server_wire import FileSystemAccessMode, NetworkDomainPermission
from .proxy_config import (
    InjectedHeaderConfig,
    MitmHookActionsConfig,
    MitmHookBodyConfig,
    MitmHookConfig,
    MitmHookMatchConfig,
    PermissionNetworkMode,
    ProfileNetworkProxyConfig,
    normalize_host,
)


def _parse_fs_access_mode(value: object) -> object:
    """`none` 是 `deny` 的 legacy 输入别名（对位 codex
    protocol/src/permissions.rs:127-129 的 `#[serde(alias = "none")]`）"""
    if value == "none":
        return FileSystemAccessMode.DENY
    return value


#: 配置侧访问模式（线上枚举 + none 别名——对位 FileSystemAccessMode 的 serde 面）
FileSystemAccessModeToml = Annotated[
    FileSystemAccessMode, BeforeValidator(_parse_fs_access_mode)
]


class WorkspaceRootsToml(RootModel[dict[str, bool]]):
    """`[permissions.<name>.workspace_roots]` 表（对位 :209-213——flatten 的
    路径→开关映射）"""

    root: dict[str, bool] = Field(default_factory=dict)

    @property
    def entries(self) -> dict[str, bool]:
        return self.root

    def enabled_roots(self) -> list[str]:
        """启用根的路径列表（对位 :215-221；BTreeMap 键序 = 排序迭代）"""
        return [path for path in sorted(self.root) if self.root[path]]


class FilesystemPermissionToml(
    RootModel[Union[FileSystemAccessModeToml, dict[str, FileSystemAccessModeToml]]]
):
    """文件系统条目权限（对位 :239-244 的 untagged 联合：字符串 = Access，
    表 = Scoped 子路径映射）"""

    @classmethod
    def access(cls, mode: FileSystemAccessMode) -> "FilesystemPermissionToml":
        return cls(mode)

    @classmethod
    def scoped(
        cls, entries: dict[str, FileSystemAccessMode]
    ) -> "FilesystemPermissionToml":
        return cls(entries)

    @property
    def is_access(self) -> bool:
        return isinstance(self.root, FileSystemAccessMode)

    @property
    def access_value(self) -> FileSystemAccessMode:
        assert isinstance(self.root, FileSystemAccessMode)
        return self.root

    @property
    def scoped_value(self) -> dict[str, FileSystemAccessMode]:
        assert isinstance(self.root, dict)
        return self.root


class FilesystemPermissionsToml(BaseModel):
    """`[permissions.<name>.filesystem]` 表（对位 :223-237）"""

    model_config = ConfigDict(populate_by_name=True)

    #: 展开不可读 glob 的最大深度（对位 :225-228；usize——负数拒收对位
    #: Rust usize 解析面；0 的拒收在编译期 validate_glob_scan_max_depth）
    glob_scan_max_depth: int | None = Field(default=None, ge=0)
    #: flatten 条目表（路径/符号根 → 权限）
    entries: dict[str, FilesystemPermissionToml] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split_flattened_entries(cls, value: Any) -> Any:
        """serde flatten 对位：TOML 原文中条目键与 `glob_scan_max_depth`
        平铺同层（对位 :229-230 的 `#[serde(flatten)]`）"""
        if isinstance(value, dict) and "entries" not in value:
            value = dict(value)
            glob_scan_max_depth = value.pop("glob_scan_max_depth", None)
            return {
                "glob_scan_max_depth": glob_scan_max_depth,
                "entries": value,
            }
        return value

    def is_empty(self) -> bool:
        """对位 :233-237"""
        return not self.entries


class NetworkDomainPermissionsToml(RootModel[dict[str, NetworkDomainPermission]]):
    """`[permissions.<name>.network.domains]` 表（对位 :246-250）"""

    root: dict[str, NetworkDomainPermission] = Field(default_factory=dict)

    @property
    def entries(self) -> dict[str, NetworkDomainPermission]:
        return self.root

    def allowed_domains(self) -> list[str] | None:
        """对位 :252-262（空集归 None；BTreeMap 键序 = 排序迭代）"""
        allowed = [
            pattern
            for pattern in sorted(self.root)
            if self.root[pattern] is NetworkDomainPermission.ALLOW
        ]
        return allowed or None

    def denied_domains(self) -> list[str] | None:
        """对位 :263-272"""
        denied = [
            pattern
            for pattern in sorted(self.root)
            if self.root[pattern] is NetworkDomainPermission.DENY
        ]
        return denied or None


class NetworkUnixSocketPermissionsToml(RootModel[dict[str, NetworkDomainPermission]]):
    """`[permissions.<name>.network.unix_sockets]` 表（对位 :293-297）——
    权限值复用线上 allow/deny 枚举（对位 `NetworkUnixSocketPermissionToml`
    :309-316，值集相同）"""

    root: dict[str, NetworkDomainPermission] = Field(default_factory=dict)

    @property
    def entries(self) -> dict[str, NetworkDomainPermission]:
        return self.root

    def allow_unix_sockets(self) -> list[str]:
        """对位 :299-307（排序迭代对位 BTreeMap）"""
        return [
            path
            for path in sorted(self.root)
            if self.root[path] is NetworkDomainPermission.ALLOW
        ]


class NetworkMitmInjectedHeaderToml(BaseModel):
    """对位 :395-402（`#[serde(default)]` 全字段可缺省）"""

    name: str = ""
    secret_env_var: str | None = None
    secret_file: str | None = None
    prefix: str | None = None

    def to_runtime(self) -> InjectedHeaderConfig:
        """对位 :580-589"""
        return InjectedHeaderConfig(
            name=self.name,
            secret_env_var=self.secret_env_var,
            secret_file=self.secret_file,
            prefix=self.prefix,
        )


class NetworkMitmActionToml(BaseModel):
    """对位 :388-393（`#[serde(default)]` 全字段可缺省）"""

    strip_request_headers: list[str] = Field(default_factory=list)
    inject_request_headers: list[NetworkMitmInjectedHeaderToml] = Field(
        default_factory=list
    )

    def is_empty(self) -> bool:
        """对位 :467-471"""
        return not self.strip_request_headers and not self.inject_request_headers


class NetworkMitmHookToml(BaseModel):
    """对位 :366-379（query/headers 带 serde default；action 必填）"""

    host: str
    methods: list[str]
    path_prefixes: list[str]
    query: dict[str, list[str]] = Field(default_factory=dict)
    headers: dict[str, list[str]] = Field(default_factory=dict)
    body: MitmHookBodyConfig | None = None
    action: list[str]

    def to_runtime(
        self, actions_by_name: dict[str, NetworkMitmActionToml] | None
    ) -> MitmHookConfig:
        """对位 :536-552"""
        return MitmHookConfig(
            host=self.host,
            matcher=MitmHookMatchConfig(
                methods=list(self.methods),
                path_prefixes=list(self.path_prefixes),
                query={key: list(values) for key, values in self.query.items()},
                headers={key: list(values) for key, values in self.headers.items()},
                body=self.body,
            ),
            actions=self._selected_actions(actions_by_name),
        )

    def _selected_actions(
        self, actions_by_name: dict[str, NetworkMitmActionToml] | None
    ) -> MitmHookActionsConfig:
        """按 action 名单展开引用（对位 :554-577——未注册名静默跳过，
        按声明序拼接 strip/inject 操作）"""
        selected = MitmHookActionsConfig()
        if actions_by_name is None:
            return selected
        for action_name in self.action:
            action = actions_by_name.get(action_name)
            if action is None:
                continue
            selected.strip_request_headers.extend(action.strip_request_headers)
            selected.inject_request_headers.extend(
                header.to_runtime() for header in action.inject_request_headers
            )
        return selected


class NetworkMitmToml(BaseModel):
    """对位 :350-357 + 自定义反序列化校验（:408-422）——
    `NetworkMitmTomlUnchecked` 的 `deny_unknown_fields` 是本族唯一真实
    serde 拒收点（其余 deny_unknown_fields 皆 schemars 层面），对位
    extra="forbid"；hooks/actions 为 IndexMap = 插入序（本侧 dict）"""

    model_config = ConfigDict(extra="forbid")

    hooks: dict[str, NetworkMitmHookToml] | None = None
    actions: dict[str, NetworkMitmActionToml] | None = None

    @model_validator(mode="after")
    def _validate_action_definitions(self) -> "NetworkMitmToml":
        """fail-closed 校验（逐行对位 :424-449——先 actions 后 hooks，
        错误文案逐字）"""
        if self.actions is not None:
            for action_name, action in self.actions.items():
                if action.is_empty():
                    raise ValueError(
                        f"network.mitm.actions.{action_name} must define at "
                        f"least one operation"
                    )
        if self.hooks is not None:
            for hook_name, hook in self.hooks.items():
                if not hook.action:
                    raise ValueError(
                        f"network.mitm.hooks.{hook_name}.action must not be empty"
                    )
        return self

    def to_runtime_hooks(
        self, actions_by_name: dict[str, NetworkMitmActionToml] | None
    ) -> list[MitmHookConfig]:
        """对位 :451-465（IndexMap 声明序 = dict 插入序）"""
        if self.hooks is None:
            return []
        return [hook.to_runtime(actions_by_name) for hook in self.hooks.values()]


class NetworkToml(BaseModel):
    """`[permissions.<name>.network]` 表（逐字段对位 :328-348）。

    serde 层面未知键忽略（`deny_unknown_fields` 仅 schemars 层面——对位
    :329 与 permissions_tests.rs 的 legacy list 键忽略用例），对位
    pydantic 默认 extra="ignore"。
    """

    enabled: bool | None = None
    proxy_url: str | None = None
    enable_socks5: bool | None = None
    socks_url: str | None = None
    enable_socks5_udp: bool | None = None
    allow_upstream_proxy: bool | None = None
    dangerously_allow_non_loopback_proxy: bool | None = None
    dangerously_allow_all_unix_sockets: bool | None = None
    mode: PermissionNetworkMode | None = None
    domains: NetworkDomainPermissionsToml | None = None
    unix_sockets: NetworkUnixSocketPermissionsToml | None = None
    #: 本地绑定许可（对位 :343-346——MXC 默认 true、其余默认 false 的注释
    #: 保留备查；本侧只承载词汇）
    allow_local_binding: bool | None = None
    mitm: NetworkMitmToml | None = None

    def apply_to_network_proxy_config(self, config: ProfileNetworkProxyConfig) -> None:
        """叠加到代理配置（逐行对位 :473-527）"""
        if self.enabled is not None:
            config.enabled = self.enabled
        if self.proxy_url is not None:
            config.proxy_url = self.proxy_url
        if self.enable_socks5 is not None:
            config.enable_socks5 = self.enable_socks5
        if self.socks_url is not None:
            config.socks_url = self.socks_url
        if self.enable_socks5_udp is not None:
            config.enable_socks5_udp = self.enable_socks5_udp
        if self.allow_upstream_proxy is not None:
            config.allow_upstream_proxy = self.allow_upstream_proxy
        if self.dangerously_allow_non_loopback_proxy is not None:
            config.dangerously_allow_non_loopback_proxy = (
                self.dangerously_allow_non_loopback_proxy
            )
        if self.dangerously_allow_all_unix_sockets is not None:
            config.dangerously_allow_all_unix_sockets = (
                self.dangerously_allow_all_unix_sockets
            )
        if self.mode is not None:
            config.mode = self.mode
        if self.domains is not None:
            overlay_network_domain_permissions(config, self.domains)
        if self.unix_sockets is not None:
            unix_sockets = dict(config.unix_sockets or {})
            for path, permission in self.unix_sockets.entries.items():
                unix_sockets[path] = permission
            config.unix_sockets = unix_sockets
        if self.allow_local_binding is not None:
            config.allow_local_binding = self.allow_local_binding
        if self.mitm is not None:
            config.mitm_hooks = self.mitm.to_runtime_hooks(self.mitm.actions)
        config.mitm = (
            config.mode is PermissionNetworkMode.LIMITED or len(config.mitm_hooks) > 0
        )

    def to_network_proxy_config(self) -> ProfileNetworkProxyConfig:
        """对位 :529-533"""
        config = ProfileNetworkProxyConfig()
        self.apply_to_network_proxy_config(config)
        return config


def overlay_network_domain_permissions(
    config: ProfileNetworkProxyConfig, domains: NetworkDomainPermissionsToml
) -> None:
    """逐条 upsert 域名权限（对位 :591-601；BTreeMap 键序 = 排序迭代）"""
    for pattern in sorted(domains.entries):
        config.upsert_domain_permission(pattern, domains.entries[pattern])


class PermissionProfileToml(BaseModel):
    """单个命名套餐（逐字段对位 :111-119）"""

    description: str | None = None
    extends: str | None = None
    workspace_roots: WorkspaceRootsToml | None = None
    filesystem: FilesystemPermissionsToml | None = None
    network: NetworkToml | None = None


class PermissionProfileResolutionErrorKind(str, Enum):
    """对位 `PermissionProfileResolutionError` 变体（:121-156）"""

    UNDEFINED_PROFILE = "undefined_profile"
    UNDEFINED_PARENT = "undefined_parent"
    UNSUPPORTED_BUILTIN_PARENT = "unsupported_builtin_parent"
    CYCLE = "cycle"
    SERIALIZE_PROFILE_TOML = "serialize_profile_toml"
    DESERIALIZE_PROFILE_TOML = "deserialize_profile_toml"


class PermissionProfileResolutionError(ValueError):
    """套餐继承解析错误（对位 :121-156——消息文案逐字对位，kind 区分变体）"""

    def __init__(
        self,
        kind: PermissionProfileResolutionErrorKind,
        message: str,
        *,
        profile_name: str | None = None,
        parent_profile_name: str | None = None,
        cycle: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.profile_name = profile_name
        self.parent_profile_name = parent_profile_name
        self.cycle = cycle

    @classmethod
    def undefined_profile(cls, profile_name: str) -> "PermissionProfileResolutionError":
        return cls(
            PermissionProfileResolutionErrorKind.UNDEFINED_PROFILE,
            f"default_permissions refers to undefined profile `{profile_name}`",
            profile_name=profile_name,
        )

    @classmethod
    def undefined_parent(
        cls, profile_name: str, parent_profile_name: str
    ) -> "PermissionProfileResolutionError":
        return cls(
            PermissionProfileResolutionErrorKind.UNDEFINED_PARENT,
            f"permissions profile `{profile_name}` extends undefined profile "
            f"`{parent_profile_name}`",
            profile_name=profile_name,
            parent_profile_name=parent_profile_name,
        )

    @classmethod
    def unsupported_builtin_parent(
        cls, profile_name: str, parent_profile_name: str
    ) -> "PermissionProfileResolutionError":
        return cls(
            PermissionProfileResolutionErrorKind.UNSUPPORTED_BUILTIN_PARENT,
            f"permissions profile `{profile_name}` cannot extend unsupported "
            f"built-in profile `{parent_profile_name}`",
            profile_name=profile_name,
            parent_profile_name=parent_profile_name,
        )

    @classmethod
    def inheritance_cycle(cls, cycle: list[str]) -> "PermissionProfileResolutionError":
        return cls(
            PermissionProfileResolutionErrorKind.CYCLE,
            "permissions profile inheritance cycle detected: " + " -> ".join(cycle),
            cycle=cycle,
        )


def _merge_toml_values(base: Any, overlay: Any) -> Any:
    """深合并（对位 config/src/merge.rs `merge_toml_values` 的核心规则
    `merge_toml_values_at_path`：双表按键递归、其余整值替换——子键覆盖
    父键）。

    裁剪登记：codex 同函数内的 shell_environment_policy 表示切换 / features
    结构化路径 / credentials 去重 / network domains 键归一四个特例，前三个
    与套餐合并无关不收；domains 键归一在套餐合并路径由
    `merge_permission_profiles` 显式触发（对位 permissions_toml.rs:178-181
    ——merge.rs 的 `[permissions, _, network, domains]` 路径匹配在单套餐
    值上本就不命中）。
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _merge_toml_values(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


def _normalize_profile_network_domains(profile: PermissionProfileToml) -> None:
    """域名键归一（对位 :193-207）"""
    if profile.network is None or profile.network.domains is None:
        return
    domains = profile.network.domains
    domains.root = {
        normalize_host(pattern): permission
        for pattern, permission in domains.entries.items()
    }


def merge_permission_profiles(
    parent: PermissionProfileToml, child: PermissionProfileToml
) -> PermissionProfileToml:
    """父先子后合并（逐行对位 :158-191——description/extends 归属选中套餐
    声明，继承来的父值不填位；双方都有 domains 时先各自归一再并表）"""
    merges_network_domains = (
        parent.network is not None
        and parent.network.domains is not None
        and child.network is not None
        and child.network.domains is not None
    )
    parent = parent.model_copy(update={"description": None, "extends": None})
    if merges_network_domains:
        _normalize_profile_network_domains(parent)
        _normalize_profile_network_domains(child)
    # 对位 TomlValue::try_from + merge_toml_values + try_into：None 字段在
    # TOML 序列化中缺席（exclude_none），深合并后回读校验
    merged = _merge_toml_values(
        parent.model_dump(mode="json", exclude_none=True),
        child.model_dump(mode="json", exclude_none=True),
    )
    try:
        return PermissionProfileToml.model_validate(merged)
    except Exception as err:
        raise PermissionProfileResolutionError(
            PermissionProfileResolutionErrorKind.DESERIALIZE_PROFILE_TOML,
            "failed to deserialize merged permissions profile while resolving "
            f"inheritance: {err}",
        ) from err


class PermissionsToml(RootModel[dict[str, PermissionProfileToml]]):
    """`[permissions]` 表（对位 :22-26——flatten 的 name→profile 映射）"""

    root: dict[str, PermissionProfileToml] = Field(default_factory=dict)

    @property
    def entries(self) -> dict[str, PermissionProfileToml]:
        return self.root

    def is_empty(self) -> bool:
        """对位 :28-31"""
        return not self.root

    def resolve_profile(
        self,
        profile_name: str,
        parent_profile: Callable[[str], PermissionProfileToml | None] | None = None,
    ) -> PermissionProfileToml:
        """解析套餐及其 extends 祖先链（逐行对位 :40-108）。

        父套餐先于子套餐合并，子键覆盖父键；返回的套餐保留**选中**套餐的
        声明元数据（description/extends）。环检测对位
        `profile_names.position` 判环（报错带环路径）。
        `parent_profile` 回调 = 内置父套餐兜底通道（codex 侧传入
        `extensible_builtin_parent_profile`，见 compile 模块）。
        """
        profile_names: list[str] = []
        profiles: list[PermissionProfileToml] = []
        next_profile_name = profile_name
        referenced_by: str | None = None

        while True:
            if next_profile_name in profile_names:
                cycle_start = profile_names.index(next_profile_name)
                cycle = [*profile_names[cycle_start:], next_profile_name]
                raise PermissionProfileResolutionError.inheritance_cycle(cycle)

            profile = self.root.get(next_profile_name)
            if profile is None and parent_profile is not None:
                profile = parent_profile(next_profile_name)
            if profile is None:
                if referenced_by is None:
                    raise PermissionProfileResolutionError.undefined_profile(
                        next_profile_name
                    )
                if next_profile_name.startswith(":"):
                    raise PermissionProfileResolutionError.unsupported_builtin_parent(
                        referenced_by, next_profile_name
                    )
                raise PermissionProfileResolutionError.undefined_parent(
                    referenced_by, next_profile_name
                )
            parent_profile_name = profile.extends

            profile_names.append(next_profile_name)

            if parent_profile_name is not None:
                profiles.append(profile)
                referenced_by = next_profile_name
                next_profile_name = parent_profile_name
                continue

            # 逐层折回：profiles 收集序为选中→…→根之子，rev 后自根向下合并，
            # 最终保留选中套餐的 description/extends（对位 :102-106）
            resolved = profile
            for child in reversed(profiles):
                resolved = merge_permission_profiles(resolved, child)
            return resolved
