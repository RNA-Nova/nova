"""资源来源信息辅助函数。

- 把 ``ResolvedResource.metadata`` / 扩展贡献路径元数据转成 ``SourceInfo``。
- 按路径精确匹配或前缀匹配查找资源对应的 ``SourceInfo``。
"""

from pathlib import Path
from typing import List, Literal, Optional, cast

from nova_harness.core.types.extensions import SourceInfo
from nova_harness.core.types.package import ResolvedResource
from nova_harness.core.types.resources.extension_paths import ResourceExtensionPathEntry


def _maybe_enum_value(value):
    """把枚举成员转为其 value；已是普通值时直接返回。"""
    if hasattr(value, "value"):
        return value.value
    return value


def source_info_from_metadata(resource: ResolvedResource) -> SourceInfo:
    """根据 ``ResolvedResource`` 的 ``PathMetadata`` 构造 ``SourceInfo``。

    保持与 ``resources/loaders/extensions.py`` 中 ``_source_info_from_metadata``
    相同的字段映射。
    """
    metadata = resource.metadata
    scope = cast(
        Literal["user", "project", "temporary"], _maybe_enum_value(metadata.scope)
    )
    origin = cast(
        Literal["package", "top-level", "local", "auto"],
        _maybe_enum_value(metadata.origin),
    )
    return SourceInfo(
        path=resource.path,
        source=metadata.source,
        scope=scope,
        origin=origin,
        base_dir=metadata.base_dir,
    )


def source_info_from_extension_entry(
    entry: ResourceExtensionPathEntry,
) -> SourceInfo:
    """把扩展通过 ``resources_discover`` 贡献的路径项转成 ``SourceInfo``。"""
    metadata = entry.metadata
    return SourceInfo(
        path=entry.path,
        source=metadata.source,
        scope=metadata.scope,
        origin=metadata.origin,
        base_dir=metadata.base_dir,
    )


def find_source_info_for_path(
    resource_path: Optional[str], source_infos: List[SourceInfo]
) -> Optional[SourceInfo]:
    """在 ``source_infos`` 中查找 ``resource_path`` 对应的 ``SourceInfo``。

    匹配规则：
    1. 精确匹配 resolved path。
    2. 前缀匹配：``resource_path`` 位于某个 ``source_info.path`` 目录下。
    3. 未找到返回 None。
    """
    if not resource_path:
        return None

    try:
        normalized_resource = Path(resource_path).resolve()
    except (OSError, ValueError):
        return None

    # 精确匹配
    for source_info in source_infos:
        try:
            normalized_source = Path(source_info.path).resolve()
        except (OSError, ValueError):
            continue
        if normalized_resource == normalized_source:
            # 返回一份 path 指向实际资源路径的副本
            return SourceInfo(
                path=resource_path,
                source=source_info.source,
                scope=source_info.scope,
                origin=source_info.origin,
                base_dir=source_info.base_dir,
            )

    # 前缀匹配
    for source_info in source_infos:
        try:
            normalized_source = Path(source_info.path).resolve()
        except (OSError, ValueError):
            continue
        try:
            normalized_resource.relative_to(normalized_source)
        except ValueError:
            continue
        return SourceInfo(
            path=resource_path,
            source=source_info.source,
            scope=source_info.scope,
            origin=source_info.origin,
            base_dir=source_info.base_dir,
        )

    return None


def _is_under(target: Path, root: Path) -> bool:
    """判断 *target* 是否位于 *root* 下（含相等）。"""
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def default_source_info_for_path(
    file_path: str,
    agent_dir: Optional[str] = None,
    cwd: Optional[str] = None,
) -> SourceInfo:
    """为没有 resolver metadata 的显式路径合成默认 ``SourceInfo``。

    对齐 TS ``getDefaultSourceInfoForPath``：位于全局/项目标准资源根
    （``backend/{skills,prompts,extensions,personas}``——前后端分治 §9 的
    后端半区）下的路径标记为 user/project scope；
    其余路径标记为 temporary scope（SDK/CLI 显式传入的临时资源）。
    """
    from nova_harness.core.config.defaults import CONFIG_DIR_NAME
    from nova_harness.core.types.package import BACKEND_HALF_DIR_NAME

    normalized = Path(file_path).resolve()

    if agent_dir:
        for name in ("skills", "prompts", "extensions", "personas"):
            root = Path(agent_dir).resolve() / BACKEND_HALF_DIR_NAME / name
            if _is_under(normalized, root):
                return SourceInfo(
                    path=file_path,
                    source="local",
                    scope="user",
                    origin="top-level",
                    base_dir=str(root),
                )

    if cwd:
        for name in ("skills", "prompts", "extensions", "personas"):
            root = Path(cwd).resolve() / CONFIG_DIR_NAME / BACKEND_HALF_DIR_NAME / name
            if _is_under(normalized, root):
                return SourceInfo(
                    path=file_path,
                    source="local",
                    scope="project",
                    origin="top-level",
                    base_dir=str(root),
                )

    return SourceInfo(
        path=file_path,
        source="local",
        scope="temporary",
        origin="top-level",
        base_dir=str(normalized) if normalized.is_dir() else str(normalized.parent),
    )


__all__ = [
    "source_info_from_metadata",
    "source_info_from_extension_entry",
    "find_source_info_for_path",
    "default_source_info_for_path",
]
