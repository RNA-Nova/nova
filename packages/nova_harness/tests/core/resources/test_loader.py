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
        from nova_harness.core.package.source.spec import (
            resolve_package_source_from_settings,
        )

        settings = self._project if local else self._global
        base = base_dir or ""
        return [
            resolve_package_source_from_settings(s, base)
            for s in (settings.packages or [])
        ]


def _make_loader(cwd: Path, agent_dir: Path, project_trusted: bool = True, **kwargs):
    settings_manager = _FakeSettingsManager(project_trusted=project_trusted)
    package_manager = PackageManager(
        agent_dir=str(agent_dir),
        cwd=str(cwd),
        settings_manager=settings_manager,
    )
    kwargs.setdefault("no_skills", True)
    kwargs.setdefault("no_extensions", True)
    kwargs.setdefault("no_prompt_templates", True)
    kwargs.setdefault("no_tools", True)
    return DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(cwd),
            agent_dir=str(agent_dir),
            settings_manager=settings_manager,
            package_manager=package_manager,
            **kwargs,
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
async def test_loader_gates_project_context_by_trust(tmp_path: Path) -> None:
    """我们的设计：项目链 context files 受 project trust 门控（全局不受门控）。"""
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
    assert "global agent context" in contents  # 用户级永远读取
    assert "project claude context" not in contents  # 不被信任的项目链不读


@pytest.mark.asyncio
async def test_context_files_override_filters_loaded_files(tmp_path: Path) -> None:
    """context_files_override 接收加载结果并可过滤（对齐 pi agentsFilesOverride）。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()

    (agent_dir / "AGENTS.md").write_text("global agent context", encoding="utf-8")
    (cwd / "CLAUDE.md").write_text("project claude context", encoding="utf-8")

    seen: list = []

    def override(files):
        seen.extend(files)
        return [f for f in files if "claude" in f.content]

    loader = _make_loader(cwd, agent_dir, context_files_override=override)
    await loader.reload()

    # override 收到了完整加载结果
    assert len(seen) == 2
    files = loader.get_context_files()
    assert [f.content for f in files] == ["project claude context"]


@pytest.mark.asyncio
async def test_context_files_override_injects_when_disabled(tmp_path: Path) -> None:
    """no_context_files=True 时基础结果为空，override 仍可注入自定义条目
    （override 在 no* 之后应用，对齐 pi）。"""
    from nova_harness.core.types.resources.context_files import ContextFile

    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    cwd.mkdir()
    agent_dir.mkdir()
    (cwd / "AGENTS.md").write_text("should be ignored", encoding="utf-8")

    def override(files):
        assert files == []
        return [ContextFile(path="<injected>", content="injected content")]

    loader = _make_loader(
        cwd,
        agent_dir,
        no_context_files=True,
        context_files_override=override,
    )
    await loader.reload()

    files = loader.get_context_files()
    assert [f.content for f in files] == ["injected content"]


def _make_loader_full(cwd: Path, agent_dir: Path) -> DefaultResourceLoader:
    """构造不禁用任何资源类型的 loader（用于 enabled 过滤与默认值测试）。"""
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
        )
    )


def test_loader_defaults_agent_dir_when_omitted(tmp_path: Path) -> None:
    """options.agent_dir 为 None 时回退到全局 get_agent_dir()（曾经未导入 NameError）。"""
    from nova_harness.core.config.defaults import get_agent_dir

    settings_manager = _FakeSettingsManager()
    package_manager = PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        settings_manager=settings_manager,
    )
    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(
            cwd=str(tmp_path),
            agent_dir=None,
            settings_manager=settings_manager,
            package_manager=package_manager,
        )
    )
    assert loader._agent_dir == str(get_agent_dir())


def test_reload_prompts_skips_disabled_resources(tmp_path: Path) -> None:
    """enabled=False 的 prompt 资源不应被加载（filters/override 排除语义）。"""
    from nova_harness.core.types.package import (
        PathMetadata,
        ResolvedPaths,
        ResolvedResource,
        SourceOrigin,
        SourceScope,
    )

    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "x.md").write_text("---\ndescription: x\n---\nbody")

    loader = _make_loader_full(tmp_path / "project", tmp_path / "agent")
    loader._resolved_paths = ResolvedPaths(
        extensions=[],
        skills=[],
        prompts=[
            ResolvedResource(
                path=str(prompt_dir),
                enabled=False,
                metadata=PathMetadata(
                    source="auto",
                    scope=SourceScope.USER,
                    origin=SourceOrigin.TOP_LEVEL,
                ),
            )
        ],
        tools=[],
        agents=[],
        diagnostics=[],
    )
    loader._reload_prompts()
    assert loader.get_prompts()["prompts"] == []


def test_reload_tools_skips_disabled_resources(tmp_path: Path) -> None:
    """enabled=False 的 tool 资源不应被加载。"""
    from nova_harness.core.types.package import (
        PathMetadata,
        ResolvedResource,
        SourceOrigin,
        SourceScope,
    )

    tool_dir = tmp_path / "tools" / "t"
    tool_dir.mkdir(parents=True)
    (tool_dir / "executor.py").write_text(
        "class Tool:\n"
        "    name = 't'\n"
        "    description = 't'\n"
        "    parameters = {}\n"
        "    def execute(self, *a, **k): return 1\n"
    )

    loader = _make_loader_full(tmp_path / "project", tmp_path / "agent")
    loader._reload_tools(
        [
            ResolvedResource(
                path=str(tool_dir),
                enabled=False,
                metadata=PathMetadata(
                    source="auto",
                    scope=SourceScope.USER,
                    origin=SourceOrigin.TOP_LEVEL,
                ),
            )
        ]
    )
    assert loader.get_tools()["tools"] == {}


def test_get_disabled_extension_names_derives_registry_names(tmp_path: Path) -> None:
    """settings 路径级裁掉的扩展：按命名规则推导注册名（目录名 / 文件 stem）。

    CapabilitySelection 报告据此区分 missing 与 disabled_by_settings。
    """
    from nova_harness.core.types.package import (
        PathMetadata,
        ResolvedPaths,
        ResolvedResource,
        SourceOrigin,
        SourceScope,
    )

    ext_dir = tmp_path / "extensions" / "drop_dir"
    ext_dir.mkdir(parents=True)
    (ext_dir / "extension.py").write_text("# dir 形态扩展", encoding="utf-8")
    ext_file = tmp_path / "extensions" / "drop_file.py"
    ext_file.write_text("# 单文件形态扩展", encoding="utf-8")
    keep_dir = tmp_path / "extensions" / "keep"
    keep_dir.mkdir()

    def _resource(path: Path, enabled: bool) -> ResolvedResource:
        return ResolvedResource(
            path=str(path),
            enabled=enabled,
            metadata=PathMetadata(
                source="auto",
                scope=SourceScope.PROJECT,
                origin=SourceOrigin.TOP_LEVEL,
            ),
        )

    loader = _make_loader_full(tmp_path / "project", tmp_path / "agent")
    # 未 resolve 前：空集
    assert loader.get_disabled_extension_names() == set()

    loader._resolved_paths = ResolvedPaths(
        extensions=[
            _resource(ext_dir, enabled=False),
            _resource(ext_file, enabled=False),
            _resource(keep_dir, enabled=True),
        ]
    )
    assert loader.get_disabled_extension_names() == {"drop_dir", "drop_file"}
