"""测试 PackageResolver 的完整解析流程。"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from nova_harness.core.package.resolver import PackageResolver
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.package_manager import (
    ResolvedPaths,
    SourceOrigin,
    SourceScope,
)


class _FakeSettingsManager:
    def __init__(self, global_settings: Settings, project_settings: Settings) -> None:
        self._global = global_settings
        self._project = project_settings
        self._project_trusted = True

    def is_project_trusted(self) -> bool:
        return self._project_trusted

    def set_project_trusted(self, value: bool) -> None:
        self._project_trusted = value

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


@pytest.fixture
def resolver_dirs(tmp_path: Path) -> dict[str, Path]:
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    (cwd / ".nova" / "extensions").mkdir(parents=True)
    (cwd / ".nova" / "skills").mkdir(parents=True)
    (cwd / ".nova" / "prompts").mkdir(parents=True)
    (cwd / ".nova" / "themes").mkdir(parents=True)
    (cwd / ".nova" / "tools").mkdir(parents=True)
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "skills").mkdir(parents=True)
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "themes").mkdir(parents=True)
    (agent_dir / "tools").mkdir(parents=True)
    return {"cwd": cwd, "agent_dir": agent_dir}


def _write_ext_file(path: Path) -> None:
    """在 path 目录下写入 extension.py。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "extension.py").write_text("def extension(nova): pass")


def _write_ext_root(path: Path) -> None:
    """把 path 本身当作扩展文件/目录写入 extension.py。"""
    if path.suffix == ".py":
        path.write_text("def extension(nova): pass")
    else:
        _write_ext_file(path)


def _write_skill(path: Path) -> None:
    path.write_text("---\nname: test-skill\ndescription: d\n---")


def _write_prompt(path: Path) -> None:
    path.write_text("---\ndescription: d\n---\nbody")


def _write_theme(path: Path) -> None:
    path.write_text("{}")


def _write_tool(path: Path) -> None:
    (path / "schema.json").write_text(json.dumps({"name": "bash"}))


@pytest.mark.asyncio
async def test_auto_discovery(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_root(cwd / ".nova" / "extensions" / "extension.py")
    _write_ext_root(agent_dir / "extensions" / "extension.py")

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    assert len(result.extensions) == 2
    proj_ext = next(r for r in result.extensions if ".nova" in r.path)
    user_ext = next(r for r in result.extensions if "agent" in r.path)
    assert proj_ext.metadata.scope == SourceScope.PROJECT
    assert proj_ext.metadata.source == "auto"
    assert user_ext.metadata.scope == SourceScope.USER


@pytest.mark.asyncio
async def test_project_trusted_false(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_root(cwd / ".nova" / "extensions" / "extension.py")
    _write_ext_root(agent_dir / "extensions" / "extension.py")

    sm = _FakeSettingsManager(Settings(), Settings())
    sm.set_project_trusted(False)
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
        project_trusted=False,
    )
    result = await resolver.resolve()

    assert len(result.extensions) == 1
    assert "agent" in result.extensions[0].path


@pytest.mark.asyncio
async def test_direct_entries_override_auto(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_file(cwd / ".nova" / "extensions" / "keep")
    _write_ext_file(cwd / ".nova" / "extensions" / "drop")

    sm = _FakeSettingsManager(
        Settings(),
        Settings(extensions=["!drop"]),
    )
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    enabled_names = {Path(r.path).name for r in result.extensions if r.enabled}
    disabled_names = {Path(r.path).name for r in result.extensions if not r.enabled}
    assert enabled_names == {"keep"}
    assert disabled_names == {"drop"}


@pytest.mark.asyncio
async def test_package_source_with_manifest(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg_dir = resolver_dirs["cwd"].parent / "pkg"
    pkg_dir.mkdir()

    ext_dir = pkg_dir / "extensions" / "pkg_ext"
    _write_ext_file(ext_dir)

    (pkg_dir / "pyproject.toml").write_text("""[tool.poetry]
name = "test-pkg"
version = "1.0.0"

[tool.nova]
extensions = ["./extensions/pkg_ext"]
""")

    sm = _FakeSettingsManager(
        Settings(packages=[str(pkg_dir)]),
        Settings(),
    )
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    assert len(result.extensions) == 1
    assert result.extensions[0].metadata.origin == SourceOrigin.PACKAGE
    assert "pkg_ext" in result.extensions[0].path


@pytest.mark.asyncio
async def test_package_source_filters(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg_dir = resolver_dirs["cwd"].parent / "pkg2"
    pkg_dir.mkdir()

    _write_ext_file(pkg_dir / "extensions" / "a")
    _write_ext_file(pkg_dir / "extensions" / "b")

    (pkg_dir / "pyproject.toml").write_text("""[tool.poetry]
name = "test-pkg2"
version = "1.0.0"

[tool.nova]
""")

    sm = _FakeSettingsManager(
        Settings(packages=[{"source": str(pkg_dir), "extensions": ["a"]}]),
        Settings(),
    )
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    names = {Path(r.path).name for r in result.extensions}
    assert names == {"a", "b"}
    enabled_names = {Path(r.path).name for r in result.extensions if r.enabled}
    assert enabled_names == {"a"}


@pytest.mark.asyncio
async def test_resolve_extension_sources_temporary(
    resolver_dirs: dict[str, Path],
) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    src_dir = resolver_dirs["cwd"].parent / "src"
    _write_ext_file(src_dir)

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = resolver.resolve_extension_sources([str(src_dir)], temporary=True)

    assert len(result.extensions) == 1
    assert result.extensions[0].metadata.scope == SourceScope.TEMPORARY


async def test_resolve_extension_sources_collects_diagnostics(resolver_dirs):
    """无效扩展源应被跳过并生成诊断信息，而不是静默消失。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = resolver.resolve_extension_sources(["path:/nonexistent/extension"])

    assert result.extensions == []
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].path == "path:/nonexistent/extension"
