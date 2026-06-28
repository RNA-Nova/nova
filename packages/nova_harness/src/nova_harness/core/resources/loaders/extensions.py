"""
扩展的发现与加载。

支持从以下位置加载 Python 扩展：
1. 显式配置的 paths
2. 项目级目录：<cwd>/.nova/extensions/
3. 全局目录：<agent_dir>/extensions/

扩展模块需暴露一个可调用对象，默认属性名为 ``extension``：

    def extension(nova):
        nova.on("session_start", lambda e: print("started"))

本模块同时提供资源加载器调用入口 ``load_extensions()``，
以及 ``ExtensionLoader`` 供需要自定义加载流程的调用方使用。
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Callable, List, Literal, Optional

from nova_harness.core.config.defaults import CONFIG_DIR_NAME, get_agent_dir
from nova_harness.core.types.diagnostics import AgentSessionRuntimeDiagnostic
from nova_harness.core.types.events import ExtensionErrorEvent
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionAPIContext,
    ExtensionEventBus,
    LoadedExtensionsResult,
)

DEFAULT_FACTORY_NAMES = ("extension", "load")


class ExtensionLoadError(Exception):
    """扩展加载失败。"""


def _find_factory(module: Any) -> Optional[Callable[..., Any]]:
    """在模块中查找扩展工厂函数。"""
    for name in DEFAULT_FACTORY_NAMES:
        factory = getattr(module, name, None)
        if callable(factory):
            return factory
    return None


def _load_module_from_file(path: Path) -> Any:
    """用 importlib 从文件路径加载模块。"""
    module_name = f"__nova_ext__.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ExtensionLoadError(f"Cannot create module spec for {path}")
    module = importlib.util.module_from_spec(spec)
    # 临时加入 sys.modules 以支持相对导入（完成后移除）
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _load_module_from_package(path: Path) -> Any:
    """加载一个包含 __init__.py 的目录作为包。"""
    init_file = path / "__init__.py"
    if init_file.exists():
        return _load_module_from_file(init_file)
    raise ExtensionLoadError(f"Package {path} has no __init__.py")


def _load_module_from_path(path: Path) -> Any:
    if path.is_file() and path.suffix == ".py":
        return _load_module_from_file(path)
    if path.is_dir():
        # 优先 extension.py，否则 __init__.py
        ext_py = path / "extension.py"
        if ext_py.exists():
            return _load_module_from_file(ext_py)
        return _load_module_from_package(path)
    raise ExtensionLoadError(f"Unsupported extension path: {path}")


class ExtensionLoader:
    """扩展加载器。"""

    def __init__(
        self,
        cwd: str,
        agent_dir: Optional[Path] = None,
        extension_api_factory: Optional[
            Callable[["Extension", "ExtensionAPIContext"], Any]
        ] = None,
    ) -> None:
        self.cwd = cwd
        self.agent_dir = agent_dir or get_agent_dir()
        self._extension_api_factory = extension_api_factory

    def discover_paths(
        self, configured_paths: Optional[List[str]] = None
    ) -> List[Path]:
        """发现所有候选扩展路径（去重）。"""
        seen: set = set()
        paths: List[Path] = []

        def add(p: Path) -> None:
            resolved = p.resolve()
            if resolved.exists() and resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)

        # 1. 显式配置
        for p in configured_paths or []:
            add(Path(p))

        # 2. 项目级目录
        project_ext_dir = Path(self.cwd) / CONFIG_DIR_NAME / "extensions"
        if project_ext_dir.exists():
            for child in sorted(project_ext_dir.iterdir()):
                add(child)

        # 3. 全局目录
        global_ext_dir = self.agent_dir / "extensions"
        if global_ext_dir.exists():
            for child in sorted(global_ext_dir.iterdir()):
                add(child)

        return paths

    def load_extension(
        self, path: Path, context: Optional[ExtensionAPIContext] = None
    ) -> Optional[Extension]:
        """加载单个扩展并返回 Extension 对象。"""
        try:
            module = _load_module_from_path(path)
        except Exception as exc:
            raise ExtensionLoadError(
                f"Failed to load extension module {path}: {exc}"
            ) from exc

        factory = _find_factory(module)
        if factory is None:
            raise ExtensionLoadError(
                f"Extension module {path} has no callable 'extension' or 'load' attribute"
            )

        # 使用目录名或文件名作为扩展显示名，避免暴露内部 module 名称
        if path.is_dir():
            name = path.name
        else:
            name = path.stem
        extension = Extension(path=str(path), name=name, module=module, factory=factory)

        if context is not None:
            if self._extension_api_factory is None:
                raise ExtensionLoadError(
                    "extension_api_factory is required to load extension factories"
                )
            api = self._extension_api_factory(extension, context)
            try:
                result = factory(api)
                if inspect.isawaitable(result):
                    # 同步加载器不支持异步工厂；需要异步加载时走 load_extensions_async
                    raise ExtensionLoadError(
                        f"Extension {path} factory is async; use load_extensions_async()"
                    )
            except Exception as exc:
                raise ExtensionLoadError(
                    f"Extension factory {path} raised: {exc}"
                ) from exc

        return extension

    async def load_extension_async(
        self, path: Path, context: ExtensionAPIContext
    ) -> Optional[Extension]:
        """异步加载单个扩展。"""
        extension = self.load_extension(path, context=None)
        if extension is None:
            return None
        if self._extension_api_factory is None:
            raise ExtensionLoadError(
                "extension_api_factory is required to load extension factories"
            )
        api = self._extension_api_factory(extension, context)
        try:
            result = extension.factory(api)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise ExtensionLoadError(f"Extension factory {path} raised: {exc}") from exc
        return extension

    async def load_extensions(
        self,
        context: ExtensionAPIContext,
        configured_paths: Optional[List[str]] = None,
    ) -> List[Extension]:
        """发现并异步加载所有扩展。"""
        paths = self.discover_paths(configured_paths)
        extensions: List[Extension] = []
        for path in paths:
            try:
                ext = await self.load_extension_async(path, context)
                if ext is not None:
                    extensions.append(ext)
            except ExtensionLoadError as exc:
                # 加载失败的扩展不中断整体流程，记录诊断信息
                context.add_diagnostic(
                    "error",
                    f"Extension {path} failed to load: {exc}",
                )
                on_error = getattr(context, "on_error", None)
                if callable(on_error):
                    on_error(
                        ExtensionErrorEvent(
                            extension_path=str(path),
                            event="load",
                            error=str(exc),
                        )
                    )
        return extensions


class _ExtensionLoadContext(ExtensionAPIContext):
    """ResourceLoader 内部使用的扩展加载上下文。"""

    def __init__(
        self,
        model_registry: Any,
        event_bus: ExtensionEventBus,
        diagnostics: List[AgentSessionRuntimeDiagnostic],
    ) -> None:
        self._model_registry = model_registry
        self._event_bus = event_bus
        self._diagnostics = diagnostics

    @property
    def event_bus(self) -> ExtensionEventBus:
        return self._event_bus

    @property
    def model_registry(self) -> Any:
        return self._model_registry

    def add_diagnostic(
        self, type: Literal["info", "warning", "error"], message: str
    ) -> None:
        self._diagnostics.append(
            AgentSessionRuntimeDiagnostic(type=type, message=message)
        )


async def load_extensions(
    cwd: str,
    agent_dir: str,
    settings_manager: Any,
    model_registry: Any,
    event_bus: ExtensionEventBus,
    additional_paths: Optional[List[str]] = None,
    no_extensions: bool = False,
    extension_api_factory: Optional[
        Callable[["Extension", "ExtensionAPIContext"], Any]
    ] = None,
) -> LoadedExtensionsResult:
    """按 Nova 规则加载扩展。"""
    if no_extensions:
        return LoadedExtensionsResult()

    loader = ExtensionLoader(
        cwd=cwd,
        agent_dir=Path(agent_dir),
        extension_api_factory=extension_api_factory,
    )

    configured_paths: List[str] = list(additional_paths or [])
    if settings_manager is not None:
        configured_paths.extend(settings_manager.get_extension_paths() or [])

    diagnostics: List[AgentSessionRuntimeDiagnostic] = []
    context = _ExtensionLoadContext(
        model_registry=model_registry,
        event_bus=event_bus,
        diagnostics=diagnostics,
    )
    extensions = await loader.load_extensions(
        context, configured_paths=configured_paths
    )

    return LoadedExtensionsResult(
        extensions=extensions,
        diagnostics=diagnostics,
    )


__all__ = [
    "ExtensionLoader",
    "ExtensionLoadError",
    "load_extensions",
]
