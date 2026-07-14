"""扩展的发现与加载（资源加载器层）。

本模块是 ``core.extensions.loader`` 的薄封装，保留资源加载器需要的
``load_extensions(...)`` 签名，同时让默认实现使用新的扩展系统。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.extensions.loader import ExtensionLoader, ExtensionLoadError
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionRuntime,
    LoadedExtensionsResult,
    SourceInfo,
)
from nova_harness.core.types.package_manager import (
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.diagnostics import ResourceDiagnostic


def _default_api_factory(
    extension: Extension,
    runtime: ExtensionRuntime,
) -> NovaExtensionAPI:
    """默认使用 NovaExtensionAPI 作为扩展工厂接收的 API。"""
    return NovaExtensionAPI(
        extension,
        runtime,
        cwd=runtime.cwd,
        event_bus=runtime.event_bus,
    )


def _source_info_from_metadata(resource: ResolvedResource) -> SourceInfo:
    """把 resolver 的 PathMetadata 转成扩展需要的 SourceInfo。"""
    metadata = resource.metadata
    scope = metadata.scope
    origin = metadata.origin
    if isinstance(scope, SourceScope):
        scope = scope.value
    if isinstance(origin, SourceOrigin):
        origin = origin.value
    return SourceInfo(
        path=resource.path,
        source=metadata.source,
        scope=scope,
        origin=origin,
        base_dir=metadata.base_dir,
    )


async def load_extensions(
    cwd: str,
    agent_dir: str,
    model_registry: Any,
    event_bus: ExtensionEventBus,
    resolved_paths: List[ResolvedResource],
    no_extensions: bool = False,
    extension_api_factory: Optional[
        Callable[[Extension, ExtensionRuntime], Any]
    ] = None,
    runtime: Optional[ExtensionRuntime] = None,
    preloaded: Optional[LoadedExtensionsResult] = None,
    extension_factories: Optional[List[Any]] = None,
) -> LoadedExtensionsResult:
    """按 Nova 规则加载扩展（供 ``DefaultResourceLoader`` 调用）。

    ``resolved_paths`` 是扩展发现的唯一来源；``extension_factories`` 支持
    程序化注入 inline 扩展工厂。

    Args:
        runtime: 复用已有的 ``ExtensionRuntime``；未提供时创建新实例。
        preloaded: pre-trust 阶段已加载的扩展结果。提供时，会按 resolved
            path 复用其中成功加载的扩展，跳过失败路径，仅加载新增路径。
        extension_factories: 内联扩展工厂列表，每个工厂接收 ``ExtensionAPI``。
    """
    if no_extensions:
        return LoadedExtensionsResult(
            runtime=runtime,
        )

    errors: List[Dict[str, str]] = []
    effective_runtime = runtime or ExtensionRuntime(
        cwd=cwd,
        event_bus=event_bus,
        model_registry=model_registry,
    )

    api_factory = extension_api_factory or _default_api_factory
    loader = ExtensionLoader(
        cwd=cwd,
        agent_dir=Path(agent_dir),
        api_factory=api_factory,
    )

    # 如果提供了 preloaded，按 resolved path 建立复用索引
    preloaded_by_path: Dict[str, Extension] = {}
    failed_preload_paths: Set[str] = set()
    if preloaded is not None:
        for ext in preloaded.extensions:
            resolved = str(Path(ext.path).resolve()) if ext.path else ""
            if resolved:
                preloaded_by_path[resolved] = ext
        for err in preloaded.errors:
            failed_path = err.get("path")
            if failed_path:
                failed_preload_paths.add(str(Path(failed_path).resolve()))

    extensions: List[Extension] = []
    remaining_paths: List[ResolvedResource] = []

    for resource in resolved_paths:
        if not resource.enabled:
            continue
        resolved_path = str(Path(resource.path).resolve())
        if resolved_path in preloaded_by_path:
            ext = preloaded_by_path[resolved_path]
            ext.source_info = _source_info_from_metadata(resource)
            extensions.append(ext)
            continue
        if resolved_path in failed_preload_paths:
            continue
        remaining_paths.append(resource)

    for resource in remaining_paths:
        path = Path(resource.path)
        try:
            ext = await loader.load_extension_async(path, effective_runtime)
            if ext is not None:
                ext.source_info = _source_info_from_metadata(resource)
                extensions.append(ext)
        except ExtensionLoadError as exc:
            error = {"path": str(path), "error": str(exc)}
            errors.append(error)
            if event_bus is not None:
                from nova_harness.core.types.events import ExtensionErrorEvent

                event_bus.emit(
                    "extension_error",
                    ExtensionErrorEvent(
                        extension_path=str(path),
                        event="load",
                        error=str(exc),
                    ),
                )

    # 加载 inline extension factories
    from nova_harness.core.extensions.loader import load_extension_from_factory

    for index, factory in enumerate(extension_factories or []):
        extension_path = f"<inline:{index + 1}>"
        try:
            ext = await load_extension_from_factory(
                factory,
                effective_runtime,
                cwd=cwd,
                api_factory=api_factory,
                extension_path=extension_path,
            )
            extensions.append(ext)
        except Exception as exc:
            error = {"path": extension_path, "error": str(exc)}
            errors.append(error)
            if event_bus is not None:
                from nova_harness.core.types.events import ExtensionErrorEvent

                event_bus.emit(
                    "extension_error",
                    ExtensionErrorEvent(
                        extension_path=extension_path,
                        event="load",
                        error=str(exc),
                    ),
                )

    merged_errors = list(preloaded.errors) if preloaded else []
    merged_errors.extend(errors)

    diagnostics = detect_extension_conflicts(extensions)
    if preloaded is not None:
        diagnostics = list(preloaded.diagnostics) + diagnostics

    return LoadedExtensionsResult(
        extensions=extensions,
        errors=merged_errors,
        runtime=effective_runtime,
        diagnostics=diagnostics,
    )


def detect_extension_conflicts(
    extensions: List[Extension],
) -> List[ResourceDiagnostic]:
    """检测扩展间同名 flag / 命令冲突，返回诊断信息。

    第一个注册者获胜，后续同名扩展产生冲突诊断。由于 Python 端扩展不再直接
    注册 tool，tool 名称冲突由 ``ToolLoader`` 负责检测。
    """
    conflicts: List[ResourceDiagnostic] = []
    flag_owners: Dict[str, str] = {}
    command_owners: Dict[str, str] = {}

    for ext in extensions:
        for command_name, command in ext.commands.items():
            resolved = command.resolved_name()
            existing = command_owners.get(resolved)
            if existing is not None and existing != ext.path:
                conflicts.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=f'Command "/{resolved}" conflicts with {existing}',
                        path=ext.path,
                    )
                )
            else:
                command_owners[resolved] = ext.path

        for flag_name in ext.flags.keys():
            existing = flag_owners.get(flag_name)
            if existing is not None and existing != ext.path:
                conflicts.append(
                    ResourceDiagnostic(
                        category="collision",
                        message=f'Flag "--{flag_name}" conflicts with {existing}',
                        path=ext.path,
                    )
                )
            else:
                flag_owners[flag_name] = ext.path

    return conflicts


__all__ = [
    "ExtensionLoader",
    "ExtensionLoadError",
    "load_extensions",
    "detect_extension_conflicts",
]
