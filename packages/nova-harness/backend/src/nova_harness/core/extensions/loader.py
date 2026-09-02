"""扩展加载器。

负责扩展发现、模块加载、``ExtensionAPI`` 与 ``ExtensionRuntime`` 创建。
Python 扩展使用 ``importlib`` 加载 ``.py`` 文件或包含 ``__init__.py`` / ``extension.py`` 的目录。
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import os
import re
import sys
import types
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nova_harness.core.config.defaults import get_agent_dir, get_project_base_dir
from nova_harness.core.extensions.api import NovaExtensionAPI
from nova_harness.core.extensions.event_bus import ExtensionEventBus
from nova_harness.core.types.events import ExtensionErrorEvent
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionFactory,
    ExtensionRuntime,
    LoadedExtensionsResult,
    SourceInfo,
    SourceOrigin,
    SourceScope,
)

DEFAULT_FACTORY_NAMES = ("extension", "load")


def _ensure_nova_ext_parent() -> None:
    """确保 ``sys.modules["__nova_ext__"]`` 存在，以支持带点的模块名。"""
    if "__nova_ext__" not in sys.modules:
        parent = types.ModuleType("__nova_ext__")
        parent.__path__ = []
        sys.modules["__nova_ext__"] = parent


def _module_name_part(path: Path) -> str:
    """根据路径生成唯一的、合法的 Python 模块名片段。

    使用 resolved path 的 hash 避免不同目录下同 stem/name 的扩展互相污染。
    """
    resolved = str(path.resolve())
    path_hash = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
    base = path.stem if path.is_file() else path.name
    safe = re.sub(r"[^0-9a-zA-Z_]", "_", base)
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    return f"{path_hash}_{safe or 'ext'}"


class ExtensionLoadError(Exception):
    """扩展加载失败。"""


def _find_factory(module: Any) -> Optional[Callable[..., Any]]:
    """在模块中查找扩展工厂函数。"""
    for name in DEFAULT_FACTORY_NAMES:
        factory = getattr(module, name, None)
        if callable(factory):
            return factory
    return None


def _purge_cached_submodules(module_name: str) -> None:
    """清除某扩展命名空间下缓存的子模块。

    扩展入口模块每次加载后从 ``sys.modules`` 弹出（fresh exec），但包内
    相对导入的子模块会留在缓存中——reload 时它们会被直接复用，导致
    helper 代码变更不生效。按命名空间前缀清理（对齐 TS
    ``clearExtensionCache`` 的语义，但范围限定在单个扩展内）。
    """
    prefix = module_name + "."
    for name in [n for n in sys.modules if n == module_name or n.startswith(prefix)]:
        sys.modules.pop(name, None)


def _load_module_from_file(path: Path, module_name: Optional[str] = None) -> Any:
    """用 importlib 从文件路径加载模块。"""
    resolved_name = module_name or f"__nova_ext__.{_module_name_part(path)}"
    _purge_cached_submodules(resolved_name)
    spec = importlib.util.spec_from_file_location(resolved_name, path)
    if spec is None or spec.loader is None:
        raise ExtensionLoadError(f"Cannot create module spec for {path}")
    _ensure_nova_ext_parent()
    module = importlib.util.module_from_spec(spec)
    sys.modules[resolved_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(resolved_name, None)
    return module


def _load_module_from_package(path: Path) -> Any:
    """加载一个包含 ``__init__.py`` 的目录作为包。

    使用 ``submodule_search_locations`` 让包内相对导入正常工作。
    """
    init_file = path / "__init__.py"
    if not init_file.exists():
        raise ExtensionLoadError(f"Package {path} has no __init__.py")

    module_name = f"__nova_ext__.{_module_name_part(path)}"
    _purge_cached_submodules(module_name)
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(path)],
    )
    if spec is None or spec.loader is None:
        raise ExtensionLoadError(f"Cannot create module spec for package {path}")
    _ensure_nova_ext_parent()
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return module


def _load_module_from_path(path: Path) -> Any:
    if path.is_file() and path.suffix == ".py":
        return _load_module_from_file(path)
    if path.is_dir():
        ext_py = path / "extension.py"
        if ext_py.exists():
            return _load_module_from_file(ext_py)
        return _load_module_from_package(path)
    raise ExtensionLoadError(f"Unsupported extension path: {path}")


def _is_inside(path: Path, base: Path) -> bool:
    """判断 *path* 是否位于 *base* 目录下（两边都会 resolve）。"""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _create_source_info(
    path: Path,
    cwd: Optional[Path] = None,
    agent_dir: Optional[Path] = None,
) -> SourceInfo:
    """根据扩展路径推断来源 scope。

    优先判断是否为项目级目录下的扩展，其次是否为全局 agent 目录下的扩展，
    其余视为临时来源。
    """
    project_base = get_project_base_dir(cwd or os.getcwd())
    agent_root = agent_dir or get_agent_dir()

    if _is_inside(path, project_base):
        scope = SourceScope.PROJECT.value
    elif _is_inside(path, agent_root):
        scope = SourceScope.USER.value
    else:
        scope = SourceScope.TEMPORARY.value

    return SourceInfo(
        path=str(path),
        source="local",
        scope=scope,
        origin=SourceOrigin.TOP_LEVEL.value,
        base_dir=str(path.parent) if path.is_file() else str(path),
    )


async def load_extension_from_factory(
    factory: ExtensionFactory,
    runtime: ExtensionRuntime,
    cwd: str,
    api_factory: Optional[Callable[[Extension, ExtensionRuntime], Any]] = None,
    extension_path: str = "<inline>",
) -> Extension:
    """从 inline factory 创建扩展。"""
    extension = Extension(
        path=extension_path,
        resolved_path=extension_path,
        name=extension_path,
        source_info=SourceInfo(
            path=extension_path,
            source="inline",
            scope="temporary",
            origin="top-level",
        ),
    )
    resolved_api_factory = api_factory or (
        lambda ext, rt: NovaExtensionAPI(ext, rt, cwd=cwd, event_bus=runtime.event_bus)
    )
    api = resolved_api_factory(extension, runtime)
    result = factory(api)
    if inspect.isawaitable(result):
        await result
    return extension


class ExtensionLoader:
    """扩展发现与加载器。"""

    def __init__(
        self,
        cwd: str,
        agent_dir: Optional[Path] = None,
        api_factory: Optional[Callable[[Extension, ExtensionRuntime], Any]] = None,
    ) -> None:
        self.cwd = cwd
        self.agent_dir = agent_dir or get_agent_dir()
        self._api_factory = api_factory
        # 模块/工厂不在公共 Extension 对象上暴露，由 loader 内部维护
        self._factories: Dict[str, Callable[..., Any]] = {}

    def load_extension(
        self,
        path: Path,
        runtime: Optional[ExtensionRuntime] = None,
    ) -> Optional[Extension]:
        """加载单个扩展并返回 ``Extension`` 对象（不执行工厂）。"""
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

        name = path.name if path.is_dir() else path.stem
        resolved = str(path.resolve())
        extension = Extension(
            path=str(path),
            resolved_path=resolved,
            name=name,
            source_info=_create_source_info(path, self.cwd, self.agent_dir),
        )
        self._factories[resolved] = factory

        if runtime is not None:
            if self._api_factory is None:
                raise ExtensionLoadError(
                    "api_factory is required to load extension factories"
                )
            api = self._api_factory(extension, runtime)
            try:
                result = factory(api)
                if inspect.isawaitable(result):
                    raise ExtensionLoadError(
                        f"Extension {path} factory is async; use load_extension_async()"
                    )
            except Exception as exc:
                raise ExtensionLoadError(
                    f"Extension factory {path} raised: {exc}"
                ) from exc

        return extension

    async def load_extension_async(
        self, path: Path, runtime: ExtensionRuntime
    ) -> Optional[Extension]:
        """异步加载单个扩展并执行工厂。"""
        extension = self.load_extension(path, runtime=None)
        if extension is None:
            return None
        if extension.resolved_path is None:
            raise ExtensionLoadError(f"Extension {path} has no resolved_path")
        factory = self._factories.get(extension.resolved_path)
        if factory is None:
            raise ExtensionLoadError(f"Extension factory for {path} is missing")
        if self._api_factory is None:
            raise ExtensionLoadError(
                "api_factory is required to load extension factories"
            )
        api = self._api_factory(extension, runtime)
        try:
            result = factory(api)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise ExtensionLoadError(f"Extension factory {path} raised: {exc}") from exc
        return extension

    async def load_extensions(
        self,
        runtime: ExtensionRuntime,
        paths: Optional[List[str]] = None,
    ) -> tuple[List[Extension], List[Dict[str, str]]]:
        """按给定路径异步加载所有扩展。

        Returns:
            (extensions, errors) 元组，errors 中每项包含 path 与 error 字符串。
        """
        extensions: List[Extension] = []
        errors: List[Dict[str, str]] = []
        for path in paths or []:
            path_obj = Path(path)
            try:
                ext = await self.load_extension_async(path_obj, runtime)
                if ext is not None:
                    extensions.append(ext)
            except ExtensionLoadError as exc:
                error = {"path": str(path), "error": str(exc)}
                errors.append(error)
                runtime.event_bus.emit(
                    "extension_error",
                    ExtensionErrorEvent(
                        extension_path=str(path),
                        event="load",
                        error=str(exc),
                    ),
                )
        return extensions, errors


async def load_extensions(
    paths: List[str],
    cwd: str,
    event_bus: Optional[ExtensionEventBus] = None,
    runtime: Optional[ExtensionRuntime] = None,
    api_factory: Optional[Callable[[Extension, ExtensionRuntime], Any]] = None,
    extension_factories: Optional[List[ExtensionFactory]] = None,
) -> LoadedExtensionsResult:
    """按路径加载扩展，支持 inline factories。"""
    resolved_event_bus = event_bus or ExtensionEventBus()
    resolved_runtime = runtime or ExtensionRuntime(
        cwd=cwd, event_bus=resolved_event_bus
    )
    resolved_api_factory = api_factory or (
        lambda ext, rt: NovaExtensionAPI(ext, rt, cwd=cwd, event_bus=resolved_event_bus)
    )

    loader = ExtensionLoader(
        cwd=cwd,
        api_factory=resolved_api_factory,
    )
    extensions, errors = await loader.load_extensions(resolved_runtime, paths=paths)

    for index, factory in enumerate(extension_factories or []):
        extension_path = f"<inline:{index + 1}>"
        try:
            ext = await load_extension_from_factory(
                factory,
                resolved_runtime,
                cwd=cwd,
                api_factory=resolved_api_factory,
                extension_path=extension_path,
            )
            extensions.append(ext)
        except Exception as exc:
            message = str(exc)
            errors.append({"path": extension_path, "error": message})

    return LoadedExtensionsResult(
        extensions=extensions,
        errors=errors,
        runtime=resolved_runtime,
    )
