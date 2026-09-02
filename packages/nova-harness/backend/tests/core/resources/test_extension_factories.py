"""Inline extension factory 支持测试。"""

from pathlib import Path
from typing import Optional

import pytest
from nova_harness.core.extensions.loader import load_extension_from_factory
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.extensions import (
    ExtensionFactory,
    ExtensionRuntime,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions


class _FakeSettingsManager:
    def __init__(self, project_trusted: bool = True):
        self._project_trusted = project_trusted
        self._global = Settings()
        self._project = Settings()

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def set_project_trusted(self, value: bool) -> None:
        self._project_trusted = value

    def reload(self) -> None:
        pass

    def get_global_settings(self) -> Settings:
        return self._global

    def get_project_settings(self) -> Settings:
        return self._project

    def get_package_sources(
        self, local: bool = False, base_dir: Optional[str] = None
    ) -> list[PackageSourceSpec]:
        from nova_harness.core.package.source.spec import (
            resolve_package_source_from_settings,
        )

        settings = self._project if local else self._global
        base = base_dir or ""
        return [
            resolve_package_source_from_settings(s, base)
            for s in (settings.packages or [])
        ]


def _make_loader(cwd: Path, agent_dir: Path, **kwargs):
    from nova_harness.core.package import PackageManager

    settings_manager = _FakeSettingsManager(project_trusted=True)
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    return DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            package_manager=package_manager,
            no_skills=True,
            no_extensions=False,
            no_prompt_templates=True,
            no_tools=True,
            **kwargs,
        )
    )


@pytest.mark.asyncio
async def test_load_extension_from_factory(tmp_path: Path):
    """直接调用 load_extension_from_factory 加载内联扩展。"""
    runtime = ExtensionRuntime(cwd=str(tmp_path))

    def factory(api):
        api.registerCommand(
            "hello",
            {"description": "from factory", "handler": lambda args, ctx: None},
        )

    ext = await load_extension_from_factory(factory, runtime, cwd=str(tmp_path))
    assert ext.path == "<inline>"
    assert "hello" in ext.commands


@pytest.mark.asyncio
async def test_load_extension_from_factory_async(tmp_path: Path):
    """支持 async factory。"""
    runtime = ExtensionRuntime(cwd=str(tmp_path))

    async def factory(api):
        api.registerCommand(
            "hello",
            {
                "description": "from async factory",
                "handler": lambda args, ctx: None,
            },
        )

    ext = await load_extension_from_factory(factory, runtime, cwd=str(tmp_path))
    assert "hello" in ext.commands


@pytest.mark.asyncio
async def test_resource_loader_loads_extension_factories(tmp_path: Path):
    """DefaultResourceLoader 通过 extension_factories 加载内联扩展。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    def factory(api):
        api.registerCommand(
            "loader_cmd",
            {"description": "x", "handler": lambda args, ctx: None},
        )

    loader = _make_loader(cwd, agent_dir, extension_factories=[factory])
    await loader.reload()

    extensions = loader.get_extensions().extensions
    assert any("loader_cmd" in ext.commands for ext in extensions)


@pytest.mark.asyncio
async def test_extension_factory_error_is_recorded(tmp_path: Path):
    """inline factory 抛出异常时应记录到 errors。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    def bad_factory(api):
        raise RuntimeError("boom")

    loader = _make_loader(cwd, agent_dir, extension_factories=[bad_factory])
    await loader.reload()

    errors = loader.get_extensions().errors
    assert any("boom" in e["error"] for e in errors)
