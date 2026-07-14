"""DefaultResourceLoader 通用行为测试。"""

from pathlib import Path
from typing import Optional

import pytest

from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
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
        from nova_harness.core.package.source import (
            resolve_package_source_from_settings,
        )

        settings = self._project if local else self._global
        base = base_dir or ""
        return [
            resolve_package_source_from_settings(s, base)
            for s in (settings.packages or [])
        ]


def _make_loader(cwd: Path, agent_dir: Path, project_trusted: bool = True):
    settings_manager = _FakeSettingsManager(project_trusted=project_trusted)
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
            no_extensions=True,
            no_prompt_templates=True,
            no_tools=True,
        )
    )


@pytest.mark.asyncio
async def test_loader_loads_context_files(tmp_path: Path) -> None:
    """DefaultResourceLoader 在 reload() 后加载项目上下文文件。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    (agent_dir / "AGENTS.md").write_text("global agent context", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("project claude context", encoding="utf-8")

    loader = _make_loader(cwd, agent_dir, project_trusted=True)
    await loader.reload()

    files = loader.get_context_files()
    contents = [f.content for f in files]
    assert "global agent context" in contents
    assert "project claude context" in contents


@pytest.mark.asyncio
async def test_loader_loads_project_context_regardless_of_trust(tmp_path: Path) -> None:
    """对齐 TS：context files 本身不受 project trust 门控。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    (agent_dir / "AGENTS.md").write_text("global agent context", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("project claude context", encoding="utf-8")

    loader = _make_loader(cwd, agent_dir, project_trusted=False)
    await loader.reload()

    files = loader.get_context_files()
    contents = [f.content for f in files]
    assert "global agent context" in contents
    assert "project claude context" in contents


@pytest.mark.asyncio
async def test_loader_reloads_themes_recursively(tmp_path: Path) -> None:
    """theme 目录应递归扫描子目录中的 .json 文件。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    themes_dir = tmp_path / "themes"
    themes_dir.mkdir()
    (themes_dir / "base.json").write_text('{"name": "base"}', encoding="utf-8")
    nested = themes_dir / "nested"
    nested.mkdir()
    (nested / "dark.json").write_text('{"name": "dark"}', encoding="utf-8")

    loader = _make_loader(cwd, agent_dir)
    loader._no_themes = False
    loader._additional_theme_paths = [str(themes_dir)]
    loader._reload_themes()

    themes = loader.get_themes()["themes"]
    assert "base" in themes
    assert "dark" in themes
