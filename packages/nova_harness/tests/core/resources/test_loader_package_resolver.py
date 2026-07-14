"""PackageManager 与 DefaultResourceLoader 集成测试。

验证当 ``DefaultResourceLoaderOptions.package_manager`` 提供时，资源发现、
优先级与包解析统一走 ``PackageManager.resolve_resources()``，子加载器不再重复扫描默认目录。
"""

import json
from pathlib import Path
from typing import Any, List, Optional

import pytest

from nova_harness.core.agent_session.services import AgentSessionServices
from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from nova_harness.core.types.resources.paths import (
    ResourceExtensionPathEntry,
    ResourceExtensionPaths,
)


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
        from nova_harness.core.package.source import (
            resolve_package_source_from_settings,
        )

        settings = self._project if local else self._global
        base = base_dir or ""
        return [
            resolve_package_source_from_settings(s, base)
            for s in (settings.packages or [])
        ]


class _FakePackageManager:
    """模拟 PackageManager，install 时会创建缺失的本地包目录。"""

    def __init__(self, agent_dir: Path, settings_manager: Any, cwd: Path) -> None:
        from nova_harness.core.package.resolver import PackageResolver

        self.agent_dir = agent_dir
        self._settings_manager = settings_manager
        self._cwd = cwd
        self.install_calls: list[tuple[str, bool]] = []
        self._resolver = PackageResolver(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
        )

    def install(self, source: str, local: bool = False) -> Any:
        self.install_calls.append((source, local))
        path = source[5:] if source.startswith("path:") else source
        pkg_dir = Path(path)
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "missing-pkg"\nversion = "1.0.0"\n'
        )
        ext_dir = pkg_dir / "extensions" / "pkg-ext"
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extension.py").write_text("def extension(api): pass")
        return None

    def list(self) -> List:
        return []

    async def resolve_resources(self, *, install_missing_packages: bool = True) -> Any:
        if install_missing_packages:
            self._ensure_packages_from_settings()
        return await self._resolver.resolve()

    def resolve_extension_sources(
        self, sources: List[str], *, temporary: bool = False, local: bool = False
    ) -> Any:
        return self._resolver.resolve_extension_sources(
            sources, temporary=temporary, local=local
        )

    def _ensure_packages_from_settings(self) -> None:
        global_settings = self._settings_manager.get_global_settings()
        project_settings = (
            self._settings_manager.get_project_settings()
            if self._settings_manager.is_project_trusted()
            else Settings()
        )
        sources: set[str] = set()
        for spec in (global_settings.packages or []) + (
            project_settings.packages or []
        ):
            source = spec if isinstance(spec, str) else spec.get("source")
            if isinstance(source, str):
                sources.add(source)
        for source in sources:
            path = source[5:] if source.startswith("path:") else source
            if not Path(path).exists():
                self.install(source)


def _write_skill(directory: Path, name: str) -> None:
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---"
    )


def _write_prompt(directory: Path, name: str) -> None:
    (directory / f"{name}.md").write_text("---\ndescription: test prompt\n---\nbody")


def _write_extension(directory: Path, name: str) -> None:
    ext_dir = directory / name
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "extension.py").write_text("def extension(api): pass")


def _write_extension_file(path: Path) -> None:
    """直接写入单个扩展入口文件（用于 manifest 多入口测试）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def extension(api): pass")


def _write_tool(tool_dir: Path, name: str) -> None:
    tool_dir.mkdir(parents=True, exist_ok=True)
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": "test tool",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text(
        "class ToolExecutor:\n"
        "    def execute(self, tool_call_id, params, signal=None, on_update=None):\n"
        "        return 'ok'\n"
    )


def _write_agent(agent_dir: Path, name: str) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "description.md").write_text(f"---\nname: {name}\n---\n{name} agent")


@pytest.fixture
def resolver_loader_dirs(tmp_path: Path) -> dict[str, Path]:
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"

    (cwd / ".nova" / "skills").mkdir(parents=True)
    (cwd / ".nova" / "prompts").mkdir(parents=True)
    (cwd / ".nova" / "extensions").mkdir(parents=True)
    (cwd / ".nova" / "tools" / "bash").mkdir(parents=True)
    (cwd / ".nova" / "agents" / "proj-agent").mkdir(parents=True)

    (agent_dir / "skills").mkdir(parents=True)
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "extensions").mkdir(parents=True)
    (agent_dir / "tools" / "curl").mkdir(parents=True)
    (agent_dir / "agents" / "user-agent").mkdir(parents=True)

    _write_skill(cwd / ".nova" / "skills", "proj-skill")
    _write_skill(agent_dir / "skills", "user-skill")
    _write_prompt(cwd / ".nova" / "prompts", "proj-prompt")
    _write_prompt(agent_dir / "prompts", "user-prompt")
    _write_extension(cwd / ".nova" / "extensions", "proj-ext")
    _write_extension(agent_dir / "extensions", "user-ext")
    _write_tool(cwd / ".nova" / "tools" / "bash", "bash")
    _write_tool(agent_dir / "tools" / "curl", "curl")
    _write_agent(cwd / ".nova" / "agents" / "proj-agent", "proj-agent")
    _write_agent(agent_dir / "agents" / "user-agent", "user-agent")

    return {"cwd": cwd, "agent_dir": agent_dir}


@pytest.mark.asyncio
async def test_resolver_drives_all_resource_types(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """package_manager 存在时，skills/prompts/extensions/tools 均按其解析结果加载。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]

    settings_manager = _FakeSettingsManager()
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=package_manager,
        )
    )
    await loader.reload()

    skills = loader.get_skills()
    assert "proj-skill" in skills["skills"]
    assert "user-skill" in skills["skills"]
    assert skills["skills"]["proj-skill"].source_label == "project"
    assert skills["skills"]["user-skill"].source_label == "user"

    prompts = loader.get_prompts()
    prompt_names = {p.name for p in prompts["prompts"]}
    assert prompt_names == {"proj-prompt", "user-prompt"}

    extensions_result = loader.get_extensions()
    ext_names = {ext.name for ext in extensions_result.extensions}
    assert ext_names == {"proj-ext", "user-ext"}
    scopes = {ext.source_info.scope for ext in extensions_result.extensions}
    assert scopes == {"project", "user"}

    agent_names = loader.get_agent_names()
    assert "proj-agent" in agent_names
    assert "user-agent" in agent_names

    # tools 不再通过顶层目录自动发现。


@pytest.mark.asyncio
async def test_resolver_does_not_duplicate_default_scan(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """package_manager 关闭子加载器默认扫描，资源不会重复加载。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]

    settings_manager = _FakeSettingsManager()
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=package_manager,
        )
    )
    await loader.reload()

    # 如果默认扫描未关闭，skill 加载器会再次扫描同一目录并产生 collision 诊断
    assert len(loader._skill_diagnostics) == 0
    assert len(loader.get_prompts()["diagnostics"]) == 0


@pytest.mark.asyncio
async def test_package_manifest_multi_extension_entries_loaded(
    tmp_path: Path,
) -> None:
    """[tool.nova.extensions] 声明多个入口时，PackageManager 会解析并加载全部扩展。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    pkg_dir = tmp_path / "pkg"
    cwd.mkdir()
    agent_dir.mkdir()
    pkg_dir.mkdir()

    _write_extension_file(pkg_dir / "ext" / "alpha.py")
    _write_extension_file(pkg_dir / "ext" / "beta.py")
    (pkg_dir / "pyproject.toml").write_text(
        """[tool.poetry]
name = "multi-ext-pkg"
version = "1.0.0"

[tool.nova]
extensions = ["ext/alpha.py", "ext/beta.py"]
""",
        encoding="utf-8",
    )

    settings_manager = _FakeSettingsManager(
        global_settings=Settings(packages=[str(pkg_dir)])
    )
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=package_manager,
        )
    )
    await loader.reload()

    extensions_result = loader.get_extensions()
    ext_paths = {ext.path for ext in extensions_result.extensions}
    assert len(extensions_result.extensions) == 2
    assert any("alpha.py" in p for p in ext_paths)
    assert any("beta.py" in p for p in ext_paths)
    assert extensions_result.extensions[0].source_info.origin == "package"


@pytest.mark.asyncio
async def test_extend_resources_merges_with_resolver_paths(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """extend_resources 追加的路径能与 package_manager 解析路径共存。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]

    extra_skill_dir = resolver_loader_dirs["cwd"].parent / "extra" / "skills"
    extra_skill_dir.mkdir(parents=True)
    _write_skill(extra_skill_dir, "extra-skill")

    settings_manager = _FakeSettingsManager()
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=package_manager,
        )
    )
    await loader.reload()

    loader.extend_resources(
        ResourceExtensionPaths(
            skill_paths=[
                ResourceExtensionPathEntry(path=str(extra_skill_dir)),
            ]
        )
    )

    skills = loader.get_skills()
    assert "extra-skill" in skills["skills"]
    assert "proj-skill" in skills["skills"]


@pytest.mark.asyncio
async def test_no_skills_with_resolver_returns_empty(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """package_manager 存在时 no_skills 仍为有效开关。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]

    settings_manager = _FakeSettingsManager()
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=package_manager,
            no_skills=True,
        )
    )
    await loader.reload()

    assert loader.get_skills() == {"skills": {}, "diagnostics": []}


@pytest.mark.asyncio
async def test_services_create_wires_package_manager_when_trusted(
    tmp_path: Path,
) -> None:
    """显式信任项目时，AgentSessionServices.create 构造的 PackageManager 会加载项目资源。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    (cwd / ".nova" / "skills").mkdir(parents=True)
    _write_skill(cwd / ".nova" / "skills", "svc-skill")
    (cwd / ".nova" / "agents" / "svc-agent").mkdir(parents=True)
    _write_agent(cwd / ".nova" / "agents" / "svc-agent", "svc-agent")

    services = await AgentSessionServices.create(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        project_trusted=True,
    )

    assert isinstance(services.resource_loader, DefaultResourceLoader)
    assert services.resource_loader._package_manager is not None
    skills = services.resource_loader.get_skills()
    assert "svc-skill" in skills["skills"]
    assert "svc-agent" in services.resource_loader.get_agent_names()


@pytest.mark.asyncio
async def test_services_create_default_untrusted_blocks_project_resources(
    tmp_path: Path,
) -> None:
    """未显式信任且未提供回调时，项目资源默认不被加载。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    (cwd / ".nova" / "skills").mkdir(parents=True)
    _write_skill(cwd / ".nova" / "skills", "svc-skill")

    services = await AgentSessionServices.create(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
    )

    assert services.settings_manager.is_project_trusted() is False
    assert "svc-skill" not in services.resource_loader.get_skills()["skills"]


@pytest.mark.asyncio
async def test_install_missing_packages_on_missing(tmp_path: Path) -> None:
    """开启 install_missing_packages 时，PackageManager 会安装缺失包。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    missing_pkg = tmp_path / "missing_pkg"

    settings_manager = _FakeSettingsManager(
        global_settings=Settings(packages=[str(missing_pkg)])
    )
    fake_pm = _FakePackageManager(agent_dir, settings_manager, cwd)
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=fake_pm,
            install_missing_packages=True,
        )
    )
    await loader.reload()

    assert len(fake_pm.install_calls) == 1
    assert fake_pm.install_calls[0][0] == str(missing_pkg)
    ext_names = {ext.name for ext in loader.get_extensions().extensions}
    assert "pkg-ext" in ext_names


@pytest.mark.asyncio
async def test_skip_missing_packages_when_disabled(tmp_path: Path) -> None:
    """关闭 install_missing_packages 时，PackageManager 不会尝试安装缺失包。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    missing_pkg = tmp_path / "missing_pkg"

    settings_manager = _FakeSettingsManager(
        global_settings=Settings(packages=[str(missing_pkg)])
    )
    fake_pm = _FakePackageManager(agent_dir, settings_manager, cwd)
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            model_registry=None,
            package_manager=fake_pm,
            install_missing_packages=False,
        )
    )
    await loader.reload()

    assert fake_pm.install_calls == []
    assert loader.get_extensions().extensions == []


@pytest.mark.asyncio
async def test_services_create_respects_untrusted_callback(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """提供 resolve_project_trust 回调且返回 False 时，项目资源被门控拦截。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]
    (cwd / ".nova" / "settings.json").write_text("{}", encoding="utf-8")

    async def _untrusted(_extensions_result: Any) -> bool:
        return False

    services = await AgentSessionServices.create(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        resolve_project_trust=_untrusted,
    )

    assert services.settings_manager.is_project_trusted() is False
    assert "proj-skill" not in services.resource_loader.get_skills()["skills"]
    assert "user-skill" in services.resource_loader.get_skills()["skills"]
    assert "proj-agent" not in services.resource_loader.get_agent_names()
    assert "user-agent" in services.resource_loader.get_agent_names()


@pytest.mark.asyncio
async def test_services_create_respects_trusted_callback(
    resolver_loader_dirs: dict[str, Path],
) -> None:
    """提供 resolve_project_trust 回调且返回 True 时，项目资源正常加载。"""
    cwd = resolver_loader_dirs["cwd"]
    agent_dir = resolver_loader_dirs["agent_dir"]
    (cwd / ".nova" / "settings.json").write_text("{}", encoding="utf-8")

    async def _trusted(_extensions_result: Any) -> bool:
        return True

    services = await AgentSessionServices.create(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        resolve_project_trust=_trusted,
    )

    assert services.settings_manager.is_project_trusted() is True
    assert "proj-skill" in services.resource_loader.get_skills()["skills"]
    assert "proj-agent" in services.resource_loader.get_agent_names()
