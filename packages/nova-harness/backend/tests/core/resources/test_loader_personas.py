"""DefaultResourceLoader 的 persona 类目集成测试（persona 升格）。

验证 resolver 驱动的 personas 加载（包类目 / settings 条目 / 自动发现 /
trust 门控）与扩展贡献通道（extend_resources 合并、reload 生命周期）。
"""

from pathlib import Path
from typing import Optional

import pytest

from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.resources.extension_paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.package import PackageManager


class _FakeSettingsManager:
    def __init__(
        self,
        global_settings: Settings = None,
        project_settings: Settings = None,
    ) -> None:
        self._global = global_settings or Settings()
        self._project = project_settings or Settings()
        self._project_trusted = True

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
        from nova_harness.package.source.spec import (
            resolve_package_source_from_settings,
        )

        settings = self._project if local else self._global
        base = base_dir or ""
        return [
            resolve_package_source_from_settings(s, base)
            for s in (settings.packages or [])
        ]


def _write_personas_root(path: Path) -> None:
    """写一个 personas 根目录（嵌套人格文件）。"""
    (path / "coding").mkdir(parents=True, exist_ok=True)
    (path / "coding" / "core.md").write_text("核心人格", encoding="utf-8")
    (path / "subagents").mkdir(parents=True, exist_ok=True)
    (path / "subagents" / "scout.md").write_text("侦察人格", encoding="utf-8")


def _make_loader(
    cwd: Path,
    agent_dir: Path,
    settings_manager: _FakeSettingsManager,
    project_trusted: Optional[bool] = None,
) -> DefaultResourceLoader:
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
        project_trusted=project_trusted,
    )
    return DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_runtime=None,
            package_manager=package_manager,
        )
    )


@pytest.fixture
def loader_dirs(tmp_path: Path) -> dict[str, Path]:
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    _write_personas_root(cwd / ".nova" / "backend" / "personas")
    _write_personas_root(agent_dir / "backend" / "personas")
    return {"cwd": cwd, "agent_dir": agent_dir}


@pytest.mark.asyncio
async def test_personas_loaded_from_auto_discovery(
    loader_dirs: dict[str, Path],
) -> None:
    """user/project 自动发现的 personas 均入注册表，按相对根路径命名。"""
    loader = _make_loader(
        loader_dirs["cwd"], loader_dirs["agent_dir"], _FakeSettingsManager()
    )
    await loader.reload()

    result = loader.get_personas()
    assert sorted(result["personas"]) == ["coding/core", "subagents/scout"]
    persona = result["personas"]["coding/core"]
    assert persona.content == "核心人格"
    assert persona.source_info is not None


@pytest.mark.asyncio
async def test_personas_project_untrusted_not_loaded(
    loader_dirs: dict[str, Path],
) -> None:
    """项目不被信任：project 级 personas 不进注册表，user 级照收。"""
    settings_manager = _FakeSettingsManager()
    settings_manager.set_project_trusted(False)
    loader = _make_loader(
        loader_dirs["cwd"],
        loader_dirs["agent_dir"],
        settings_manager,
        project_trusted=False,
    )
    await loader.reload()

    result = loader.get_personas()
    assert sorted(result["personas"]) == ["coding/core", "subagents/scout"]
    scopes = {p.source_info.scope for p in result["personas"].values()}
    assert scopes == {"user"}


@pytest.mark.asyncio
async def test_personas_from_package_manifest(tmp_path: Path) -> None:
    """包 [tool.nova] personas 类目：包内 personas 根入注册表（origin=package）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    _write_personas_root(pkg_dir / "personas")
    (pkg_dir / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "pkg-personas"\nversion = "1.0.0"\n'
        '\n[tool.nova]\npersonas = ["./personas/"]\n'
    )

    settings_manager = _FakeSettingsManager(
        global_settings=Settings(packages=[str(pkg_dir)])
    )
    loader = _make_loader(cwd, agent_dir, settings_manager)
    await loader.reload()

    result = loader.get_personas()
    assert sorted(result["personas"]) == ["coding/core", "subagents/scout"]
    persona = result["personas"]["coding/core"]
    assert persona.source_info is not None
    assert persona.source_info.origin == "package"


@pytest.mark.asyncio
async def test_extend_resources_merges_persona_paths(
    loader_dirs: dict[str, Path],
) -> None:
    """扩展贡献的 persona 路径与 resolver 结果合并（同名 first-wins 不覆盖）。"""
    loader = _make_loader(
        loader_dirs["cwd"], loader_dirs["agent_dir"], _FakeSettingsManager()
    )
    await loader.reload()

    ext_root = loader_dirs["cwd"] / "ext-personas"
    _write_personas_root(ext_root)
    (ext_root / "extra.md").write_text("扩展人格", encoding="utf-8")

    loader.extend_resources(
        ResourceExtensionPaths(
            persona_paths=[
                ResourceExtensionPathEntry(
                    path=str(ext_root), extension_path="/fake/ext.py"
                )
            ]
        )
    )

    result = loader.get_personas()
    names = sorted(result["personas"])
    # 扩展贡献的 extra 进注册表；与自动发现同名的 coding/core 保持先者胜
    assert "extra" in names
    assert "coding/core" in names
    assert result["personas"]["coding/core"].content == "核心人格"


@pytest.mark.asyncio
async def test_extend_resources_persona_paths_cleared_on_reload(
    loader_dirs: dict[str, Path],
) -> None:
    """扩展贡献路径的生命周期绑定扩展加载：全量 reload 后清空重贡献。"""
    loader = _make_loader(
        loader_dirs["cwd"], loader_dirs["agent_dir"], _FakeSettingsManager()
    )
    await loader.reload()

    ext_root = loader_dirs["cwd"] / "ext-personas"
    ext_root.mkdir(parents=True, exist_ok=True)
    (ext_root / "extra.md").write_text("扩展人格", encoding="utf-8")

    loader.extend_resources(
        ResourceExtensionPaths(
            persona_paths=[ResourceExtensionPathEntry(path=str(ext_root))]
        )
    )
    assert "extra" in loader.get_personas()["personas"]

    await loader.reload()
    assert "extra" not in loader.get_personas()["personas"]
