"""
扩展加载器（loaders/extensions.py）单元测试。
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nova_harness.core.resources.loaders.extensions import (
    ExtensionLoader,
    ExtensionLoadError,
    _find_factory,
    _load_module_from_file,
    _load_module_from_package,
    _load_module_from_path,
    load_extensions,
)
from nova_harness.core.types.extensions import ExtensionEventBus


def test_find_factory_prefers_extension():
    """_find_factory 优先返回 extension 属性。"""
    module = MagicMock()
    module.extension = lambda: None
    module.load = lambda: None
    assert _find_factory(module) is module.extension


def test_find_factory_falls_back_to_load():
    """没有 extension 时回退到 load。"""
    module = SimpleNamespace()
    module.load = lambda: None
    assert _find_factory(module) is module.load


def test_find_factory_returns_none_when_missing():
    """没有可用工厂时返回 None。"""
    assert _find_factory(SimpleNamespace()) is None


def test_load_module_from_file(tmp_path: Path):
    """_load_module_from_file 应执行指定 Python 文件。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text("value = 42\n", encoding="utf-8")
    module = _load_module_from_file(ext_file)
    assert module.value == 42


def test_load_module_from_file_removes_from_sys_modules(tmp_path: Path):
    """加载完成后应清理临时 sys.modules 条目。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text("value = 1\n", encoding="utf-8")
    _load_module_from_file(ext_file)
    assert "__nova_ext__.ext" not in sys.modules


def test_load_module_from_package(tmp_path: Path):
    """_load_module_from_package 加载含 __init__.py 的目录。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("value = 99\n", encoding="utf-8")
    module = _load_module_from_package(pkg)
    assert module.value == 99


def test_load_module_from_package_missing_init(tmp_path: Path):
    """缺少 __init__.py 时应抛 ExtensionLoadError。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    with pytest.raises(ExtensionLoadError):
        _load_module_from_package(pkg)


def test_load_module_from_path_file(tmp_path: Path):
    """文件路径直接加载。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text("value = 1\n", encoding="utf-8")
    module = _load_module_from_path(ext_file)
    assert module.value == 1


def test_load_module_from_path_dir_extension_py(tmp_path: Path):
    """目录含 extension.py 时优先加载。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "extension.py").write_text("value = 2\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("value = 3\n", encoding="utf-8")
    module = _load_module_from_path(pkg)
    assert module.value == 2


def test_load_module_from_path_dir_init_py(tmp_path: Path):
    """目录只有 __init__.py 时加载。"""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("value = 4\n", encoding="utf-8")
    module = _load_module_from_path(pkg)
    assert module.value == 4


def test_load_module_from_path_unsupported(tmp_path: Path):
    """不支持的文件类型抛 ExtensionLoadError。"""
    txt = tmp_path / "ext.txt"
    txt.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(ExtensionLoadError):
        _load_module_from_path(txt)


def test_loader_discover_paths(tmp_path: Path):
    """discover_paths 应合并显式路径、项目目录与全局目录。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / ".nova" / "agent"
    project_ext = cwd / ".nova" / "extensions" / "proj_ext.py"
    global_ext = agent_dir / "extensions" / "global_ext.py"
    project_ext.parent.mkdir(parents=True)
    global_ext.parent.mkdir(parents=True)
    project_ext.write_text("value = 1", encoding="utf-8")
    global_ext.write_text("value = 2", encoding="utf-8")
    explicit = tmp_path / "explicit.py"
    explicit.write_text("value = 3", encoding="utf-8")

    loader = ExtensionLoader(cwd=str(cwd), agent_dir=agent_dir)
    paths = loader.discover_paths([str(explicit)])
    names = {p.name for p in paths}
    assert "proj_ext.py" in names
    assert "global_ext.py" in names
    assert "explicit.py" in names


def test_loader_discover_paths_deduplicates(tmp_path: Path):
    """discover_paths 应对重复路径去重。"""
    ext = tmp_path / "ext.py"
    ext.write_text("value = 1", encoding="utf-8")
    loader = ExtensionLoader(cwd=str(tmp_path))
    paths = loader.discover_paths([str(ext), str(ext)])
    assert len(paths) == 1


def test_loader_load_extension_returns_without_factory_context(tmp_path: Path):
    """未提供 context 时仅解析模块并返回 Extension 对象。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "def extension(nova):\n    nova.on('x', lambda e: None)\n",
        encoding="utf-8",
    )
    loader = ExtensionLoader(cwd=str(tmp_path))
    ext = loader.load_extension(ext_file)
    assert ext.name == "ext"
    assert ext.factory is not None


def test_loader_load_extension_missing_factory(tmp_path: Path):
    """模块没有 callable 工厂时抛 ExtensionLoadError。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text("value = 1\n", encoding="utf-8")
    loader = ExtensionLoader(cwd=str(tmp_path))
    with pytest.raises(ExtensionLoadError):
        loader.load_extension(ext_file)


def test_loader_load_extension_factory_raises(tmp_path: Path):
    """同步工厂抛出异常时应转换为 ExtensionLoadError。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "def extension(nova):\n    raise ValueError('boom')\n",
        encoding="utf-8",
    )
    loader = ExtensionLoader(
        cwd=str(tmp_path),
        extension_api_factory=lambda ext, ctx: MagicMock(),
    )
    with pytest.raises(ExtensionLoadError):
        loader.load_extension(ext_file, context=MagicMock())


def test_loader_load_extension_async_factory_in_sync_loader(tmp_path: Path):
    """同步 load_extension 遇到 async 工厂应抛错。"""
    import warnings

    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "async def extension(nova):\n    pass\n",
        encoding="utf-8",
    )
    loader = ExtensionLoader(
        cwd=str(tmp_path),
        extension_api_factory=lambda ext, ctx: MagicMock(),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(ExtensionLoadError):
            loader.load_extension(ext_file, context=MagicMock())


def test_loader_load_extension_requires_factory_when_context_given(tmp_path: Path):
    """提供 context 但未配置 extension_api_factory 时应抛错。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "def extension(nova):\n    pass\n",
        encoding="utf-8",
    )
    loader = ExtensionLoader(cwd=str(tmp_path))
    with pytest.raises(ExtensionLoadError):
        loader.load_extension(ext_file, context=MagicMock())


@pytest.mark.asyncio
async def test_loader_load_extension_async_success(tmp_path: Path):
    """异步加载单个扩展成功。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "def extension(nova):\n    pass\n",
        encoding="utf-8",
    )
    api = MagicMock()
    loader = ExtensionLoader(
        cwd=str(tmp_path),
        extension_api_factory=lambda ext, ctx: api,
    )
    ext = await loader.load_extension_async(ext_file, context=MagicMock())
    assert ext is not None
    assert ext.name == "ext"


@pytest.mark.asyncio
async def test_loader_load_extension_async_factory_awaited(tmp_path: Path):
    """异步加载应 await async 工厂。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "async def extension(nova):\n    nova.value = 1\n",
        encoding="utf-8",
    )
    api = MagicMock()
    loader = ExtensionLoader(
        cwd=str(tmp_path),
        extension_api_factory=lambda ext, ctx: api,
    )
    ext = await loader.load_extension_async(ext_file, context=MagicMock())
    assert ext is not None


@pytest.mark.asyncio
async def test_loader_load_extensions_records_diagnostics_and_error_event(
    tmp_path: Path,
):
    """加载失败时记录诊断并触发 on_error 回调。"""
    ext_file = tmp_path / "bad.py"
    ext_file.write_text("value = 1\n", encoding="utf-8")
    loader = ExtensionLoader(cwd=str(tmp_path))

    context = MagicMock()
    context.on_error = MagicMock()
    exts = await loader.load_extensions(context, configured_paths=[str(ext_file)])
    assert exts == []
    context.add_diagnostic.assert_called_once()
    context.on_error.assert_called_once()


@pytest.mark.asyncio
async def test_load_extensions_top_level_no_extensions():
    """no_extensions=True 时直接返回空结果。"""
    result = await load_extensions(
        cwd="/tmp",
        agent_dir="/tmp/.nova/agent",
        settings_manager=None,
        model_registry=None,
        event_bus=ExtensionEventBus(),
        no_extensions=True,
    )
    assert result.extensions == []
    assert result.diagnostics == []


@pytest.mark.asyncio
async def test_load_extensions_top_level_uses_settings_paths(tmp_path: Path):
    """load_extensions 应从 settings_manager 读取扩展路径。"""
    ext_file = tmp_path / "ext.py"
    ext_file.write_text(
        "def extension(nova):\n    pass\n",
        encoding="utf-8",
    )
    settings_manager = MagicMock()
    settings_manager.get_extension_paths.return_value = [str(ext_file)]

    result = await load_extensions(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / ".nova" / "agent"),
        settings_manager=settings_manager,
        model_registry=None,
        event_bus=ExtensionEventBus(),
        extension_api_factory=lambda ext, ctx: MagicMock(),
    )
    assert len(result.extensions) == 1
    assert result.extensions[0].name == "ext"
