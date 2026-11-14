"""权限套餐选择解析（对位 codex 锚点 39f4277087
`core/src/config/permission_profile_selection.rs` 全文 +
`core/src/config/mod.rs:4795-4953` 的选择/校验函数族 + :3530-3541 的
default_permissions 缺位拒收）。

范围登记：`ConfigRequirementsToml` 是 codex 托管需求层的大词汇——本模块
只收**权限选择子集**（`allowed_permission_profiles` / `default_permissions`
/ `[permissions]` 托管套餐表），其余字段归后续批次；override/persisted
两通道（mod.rs:4826 签名中的 `default_permissions_override` /
`persisted_profile_id`）属 Config 装配面，随 loader 接线批次落地。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .compile import (
    is_builtin_permission_profile_name,
    validate_user_permission_profile_names,
)
from .runtime import (
    BUILT_IN_PERMISSION_PROFILE_READ_ONLY,
    BUILT_IN_PERMISSION_PROFILE_WORKSPACE,
)
from .toml import PermissionProfileToml, PermissionsToml


class FilesystemRequirementsToml(BaseModel):
    """需求层文件系统约束（对位 config/src/config_requirements.rs:640-671
    的自定义反序列化——只允许 `deny_read`，携带套餐形状键即拒）"""

    deny_read: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_profile_shaped_keys(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            key in value
            for key in (
                "description",
                "extends",
                "workspace_roots",
                "filesystem",
                "network",
            )
        ):
            raise ValueError(
                "`permissions.filesystem` is reserved for requirements-level "
                "filesystem constraints and cannot define a profile"
            )
        return value


class PermissionsRequirementsToml(BaseModel):
    """需求层 `[permissions]` 表（对位 config_requirements.rs:675-684——
    `filesystem` 保留键 + flatten 托管套餐映射）"""

    filesystem: FilesystemRequirementsToml | None = None
    profiles: dict[str, PermissionProfileToml] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split_reserved_filesystem_key(cls, value: Any) -> Any:
        """对位 flatten——`filesystem` 恒绑定保留字段，其余键皆为托管套餐"""
        if isinstance(value, dict) and "profiles" not in value:
            value = dict(value)
            filesystem = value.pop("filesystem", None)
            return {"filesystem": filesystem, "profiles": value}
        return value


class PermissionRequirementsToml(BaseModel):
    """需求层权限选择子集（对位 `ConfigRequirementsToml`
    （config_requirements.rs:1053-1054 + :1077）中权限选择相关的三字段）"""

    allowed_permission_profiles: dict[str, bool] | None = None
    default_permissions: str | None = None
    permissions: PermissionsRequirementsToml | None = None


def _is_permission_allowed(
    allowed_permission_profiles: dict[str, bool], profile_id: str
) -> bool:
    """对位 mod.rs:4946-4953"""
    return allowed_permission_profiles.get(profile_id, False)


def _implicit_default_permissions(
    allowed_permission_profiles: dict[str, bool],
) -> str | None:
    """对位 mod.rs:4935-4944——双内置皆放行时隐式缺省 :workspace"""
    if _is_permission_allowed(
        allowed_permission_profiles, BUILT_IN_PERMISSION_PROFILE_WORKSPACE
    ) and _is_permission_allowed(
        allowed_permission_profiles, BUILT_IN_PERMISSION_PROFILE_READ_ONLY
    ):
        return BUILT_IN_PERMISSION_PROFILE_WORKSPACE
    return None


def merge_managed_permission_profiles(
    configured_permissions: PermissionsToml | None,
    requirements: PermissionRequirementsToml | None,
) -> PermissionsToml | None:
    """托管套餐并入配置套餐（对位 mod.rs:4795-4823——同名冲突响亮报错）"""
    managed_profiles = (
        requirements.permissions.profiles
        if requirements is not None and requirements.permissions is not None
        else None
    )
    if not managed_profiles:
        return configured_permissions
    merged = (
        PermissionsToml(dict(configured_permissions.entries))
        if configured_permissions is not None
        else PermissionsToml()
    )
    for profile_id, managed_profile in managed_profiles.items():
        if profile_id in merged.entries:
            raise ValueError(
                f"requirements.toml permissions profile `{profile_id}` "
                "conflicts with a config-defined profile of the same name"
            )
        merged.entries[profile_id] = managed_profile
    return merged


def validate_required_permission_profile_catalog(
    requirements: PermissionRequirementsToml | None,
    available_permissions: PermissionsToml | None,
) -> None:
    """需求层引用的套餐必须可解析（逐行对位 mod.rs:4880-4933）"""
    if requirements is None:
        return

    def is_known_profile(profile_id: str) -> bool:
        return is_builtin_permission_profile_name(profile_id) or (
            available_permissions is not None
            and profile_id in available_permissions.entries
        )

    allowed = requirements.allowed_permission_profiles
    if allowed is None:
        if requirements.default_permissions is not None:
            raise ValueError(
                "requirements.toml default_permissions requires "
                "allowed_permission_profiles"
            )
        return
    for profile_id in allowed:
        if not is_known_profile(profile_id):
            raise ValueError(
                "requirements.toml allowed_permission_profiles refers to "
                f"undefined profile `{profile_id}`"
            )
    default_permissions = (
        requirements.default_permissions or _implicit_default_permissions(allowed)
    )
    if default_permissions is None:
        raise ValueError(
            "requirements.toml default_permissions must be set unless "
            "allowed_permission_profiles allows both `:workspace` and "
            "`:read-only`"
        )
    if not _is_permission_allowed(allowed, default_permissions):
        raise ValueError(
            f"requirements.toml default_permissions `{default_permissions}` "
            "must be allowed by allowed_permission_profiles"
        )


@dataclass(frozen=True)
class EffectivePermissionSelection:
    """有效选择结果（对位 mod.rs 同名结构的权限选择字段）"""

    profiles: PermissionsToml | None
    selected_profile_id: str | None
    #: 需求层 allowlist 存在 = 强制套餐选择（对位 mod.rs:4844-4847）
    requirements_force_profile_selection: bool


def resolve_default_permissions(
    configured_default_permissions: str | None,
    requirements: PermissionRequirementsToml | None,
    startup_warnings: list[str] | None = None,
) -> str | None:
    """缺省套餐裁决（逐行对位 mod.rs:4854-4878 `resolve_default_permissions`
    ——override/persisted 通道未随本批移植，对位省略）"""
    selected = configured_default_permissions
    allowed = (
        requirements.allowed_permission_profiles if requirements is not None else None
    )
    if allowed is None:
        return selected
    fallback = None
    if requirements is not None:
        fallback = requirements.default_permissions or _implicit_default_permissions(
            allowed
        )
    if fallback is None:
        raise ValueError(
            "requirements.toml default_permissions must be set unless "
            "allowed_permission_profiles allows both `:workspace` and "
            "`:read-only`"
        )
    if selected is None:
        return fallback
    if _is_permission_allowed(allowed, selected):
        return selected
    if startup_warnings is not None:
        startup_warnings.append(
            "Configured value for `permission_profile` is disallowed by "
            f"requirements; falling back from `{selected}` to required value "
            f"`{fallback}`."
        )
    return fallback


def resolve_effective_permission_selection(
    configured_profiles: PermissionsToml | None,
    configured_default_profile_id: str | None,
    requirements: PermissionRequirementsToml | None,
    startup_warnings: list[str] | None = None,
) -> EffectivePermissionSelection:
    """有效套餐选择（对位 mod.rs:4826-4851——override/persisted 两通道属
    Config 装配面，本批不收）"""
    profiles = merge_managed_permission_profiles(configured_profiles, requirements)
    validate_user_permission_profile_names(profiles)
    validate_required_permission_profile_catalog(requirements, profiles)
    selected_profile_id = resolve_default_permissions(
        configured_default_profile_id, requirements, startup_warnings
    )
    return EffectivePermissionSelection(
        profiles=profiles,
        selected_profile_id=selected_profile_id,
        requirements_force_profile_selection=(
            requirements is not None
            and requirements.allowed_permission_profiles is not None
        ),
    )


def resolve_config_permission_selection(
    configured_profiles: PermissionsToml | None,
    configured_default_profile_id: str | None,
    requirements: PermissionRequirementsToml | None = None,
    *,
    legacy_syntax: bool = False,
    startup_warnings: list[str] | None = None,
) -> EffectivePermissionSelection:
    """配置层选择单点（对位 mod.rs:3521-3541 的装配段）。

    `legacy_syntax` 对位 `PermissionConfigSyntax::Legacy`（配置含 legacy
    sandbox_mode 面）——legacy 语法下豁免"有套餐表却无 default_permissions"
    的拒收。
    """
    selection = resolve_effective_permission_selection(
        configured_profiles,
        configured_default_profile_id,
        requirements,
        startup_warnings,
    )
    if (
        selection.profiles is not None
        and not selection.profiles.is_empty()
        and not legacy_syntax
        and selection.selected_profile_id is None
        and not selection.requirements_force_profile_selection
    ):
        raise ValueError(
            "config defines `[permissions]` profiles but does not set "
            "`default_permissions`"
        )
    return selection


@dataclass(frozen=True)
class ResolvedPermissionProfileSelection:
    """选中的套餐与未编译目录（对位 permission_profile_selection.rs:6-11——
    托管目录保持未编译，executor 侧再解释）"""

    profile_id: str | None
    profiles: PermissionsToml | None


def resolve_permission_profile_selection(
    configured_profiles: PermissionsToml | None,
    configured_default_profile_id: str | None,
    requirements: PermissionRequirementsToml | None,
) -> ResolvedPermissionProfileSelection:
    """对位 permission_profile_selection.rs:17-35（托管套餐与 allowlist
    按 Core 常规选择规则应用，目录不编译）"""
    selection = resolve_effective_permission_selection(
        configured_profiles,
        configured_default_profile_id,
        requirements,
        None,
    )
    return ResolvedPermissionProfileSelection(
        profile_id=selection.selected_profile_id,
        profiles=selection.profiles,
    )
