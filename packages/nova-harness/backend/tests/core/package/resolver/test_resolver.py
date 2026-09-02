"""测试 PackageResolver 的完整解析流程。"""

import json
from pathlib import Path
from typing import Any, Optional

import pytest
from nova_harness.core.package.resolve.resolver import PackageResolver
from nova_harness.core.types.config.settings import PackageSourceSpec, Settings
from nova_harness.core.types.package import (
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
        from nova_harness.core.package.source.spec import (
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
    """新旧布局（前后端分治 §9）：散养资源归 <base>/backend/ 半区，agents 平级。"""
    cwd = tmp_path / "project"
    agent_dir = tmp_path / "agent"
    (cwd / ".nova" / "backend" / "extensions").mkdir(parents=True)
    (cwd / ".nova" / "backend" / "skills").mkdir(parents=True)
    (cwd / ".nova" / "backend" / "prompts").mkdir(parents=True)
    (agent_dir / "backend" / "extensions").mkdir(parents=True)
    (agent_dir / "backend" / "skills").mkdir(parents=True)
    (agent_dir / "backend" / "prompts").mkdir(parents=True)
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


def _write_prompt(path: Path) -> None:
    path.write_text("---\ndescription: d\n---\nbody")


@pytest.mark.asyncio
async def test_auto_discovery(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_root(cwd / ".nova" / "backend" / "extensions" / "extension.py")
    _write_ext_root(agent_dir / "backend" / "extensions" / "extension.py")

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
async def test_legacy_flat_dirs_not_discovered(resolver_dirs: dict[str, Path]) -> None:
    """旧布局（散养资源直挂 <base>/<type>）不再被自动发现。

    迁移归服务装配期（``AgentSessionServices.create`` → ``migrate_backend_layout``），
    resolver 只认新位；未迁移的旧位目录保持静默忽略。
    """
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    (cwd / ".nova" / "extensions").mkdir(parents=True)
    (agent_dir / "extensions").mkdir(parents=True)
    _write_ext_root(cwd / ".nova" / "extensions" / "extension.py")
    _write_ext_root(agent_dir / "extensions" / "extension.py")

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    assert result.extensions == []


@pytest.mark.asyncio
async def test_project_trusted_false(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_root(cwd / ".nova" / "backend" / "extensions" / "extension.py")
    _write_ext_root(agent_dir / "backend" / "extensions" / "extension.py")

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
async def test_ancestor_skills_exclude_home_agents_dir(
    resolver_dirs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cwd 位于 $HOME 下时，~/.agents/skills 保持 user scope，不被升格为 project（对齐 TS）。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    fake_home = tmp_path / "home"
    home_skills = fake_home / ".agents" / "skills"
    skill_dir = home_skills / "home-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: home-skill\ndescription: d\n---", encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(
        "nova_harness.core.package.resolve.resolver.collect_ancestor_agents_skills_dirs",
        lambda start_dir, stop_at_git_root=True: [str(home_skills.resolve())],
    )

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
    )
    result = await resolver.resolve()

    # skill 在解析期被扫描（agents 模式），以 user scope 出现且仅出现一次；
    # base_dir 指向其 .agents 目录（对齐 TS 逐组 metadata）。
    matching = [r for r in result.skills if "home-skill" in r.path]
    assert len(matching) == 1
    assert matching[0].metadata.scope == SourceScope.USER
    assert matching[0].metadata.base_dir == str((fake_home / ".agents").resolve())


@pytest.mark.asyncio
async def test_direct_entries_override_auto(resolver_dirs: dict[str, Path]) -> None:
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    _write_ext_file(cwd / ".nova" / "backend" / "extensions" / "keep")
    _write_ext_file(cwd / ".nova" / "backend" / "extensions" / "drop")

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


def _make_skill_pkg(path: Path, skill_names: list[str]) -> None:
    """构造一个含多个 skill 的包（manifest 显式声明）。"""
    path.mkdir(parents=True)
    skills_dir = path / "skills"
    rel = []
    for name in skill_names:
        skill_dir = skills_dir / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---")
        rel.append(f"./skills/{name}")
    (path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "pkg-x"\nversion = "1.0.0"\n'
        "\n[tool.nova]\nskills = [" + ", ".join(f'"{r}"' for r in rel) + "]\n"
    )


@pytest.mark.asyncio
async def test_autoload_false_delta_disables_single_resource(
    resolver_dirs: dict[str, Path],
) -> None:
    """autoload=false + -path：在 user 自动加载的基础上局部禁用单个资源。

    强制模式（``+``/``-``）是精确路径匹配（对齐 TS：不按 basename 命中）。
    """
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg = resolver_dirs["cwd"].parent / "pkg-x"
    _make_skill_pkg(pkg, ["a", "b", "c"])

    global_settings = Settings(packages=[str(pkg)])
    project_settings = Settings(
        packages=[{"source": str(pkg), "autoload": False, "skills": ["-skills/b"]}]
    )
    sm = _FakeSettingsManager(global_settings, project_settings)
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    states = {Path(r.path).name: r.enabled for r in result.skills}
    assert states == {"a": True, "b": False, "c": True}


@pytest.mark.asyncio
async def test_autoload_false_delta_disable_all_then_pick_back(
    resolver_dirs: dict[str, Path],
) -> None:
    """autoload=false + !* + +path：禁用自动加载后只挑回一个资源。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg = resolver_dirs["cwd"].parent / "pkg-x"
    _make_skill_pkg(pkg, ["a", "b", "c"])

    global_settings = Settings(packages=[str(pkg)])
    project_settings = Settings(
        packages=[
            {"source": str(pkg), "autoload": False, "skills": ["!*", "+skills/a"]}
        ]
    )
    sm = _FakeSettingsManager(global_settings, project_settings)
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    states = {Path(r.path).name: r.enabled for r in result.skills}
    assert states == {"a": True, "b": False, "c": False}


@pytest.mark.asyncio
async def test_project_entry_without_autoload_still_overrides_user(
    resolver_dirs: dict[str, Path],
) -> None:
    """无 autoload 字段时保持现有语义：project 同 identity 条目整体覆盖 user。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg = resolver_dirs["cwd"].parent / "pkg-x"
    _make_skill_pkg(pkg, ["a", "b"])

    global_settings = Settings(packages=[str(pkg)])
    # project 用 filters 白名单（非 autoload=false）：user 条目应被去重。
    project_settings = Settings(packages=[{"source": str(pkg), "skills": ["a"]}])
    sm = _FakeSettingsManager(global_settings, project_settings)
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    states = {Path(r.path).name: r.enabled for r in result.skills}
    assert states == {"a": True, "b": False}


@pytest.mark.asyncio
async def test_direct_entries_absolute_glob(resolver_dirs: dict[str, Path]) -> None:
    """settings 直接条目支持绝对 glob（pathlib 不支持绝对模式，曾让整个 resolve 崩溃）。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    ext_dir = resolver_dirs["cwd"].parent / "ext-abs"
    _write_ext_file(ext_dir / "a")
    _write_ext_file(ext_dir / "b")

    sm = _FakeSettingsManager(
        Settings(),
        Settings(extensions=[f"{ext_dir}/*/extension.py"]),
    )
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    names = {Path(r.path).parent.name for r in result.extensions if "ext-abs" in r.path}
    assert names == {"a", "b"}


@pytest.mark.asyncio
async def test_agents_skills_scanned_in_agents_mode(
    resolver_dirs: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """.agents/skills 以 agents 模式扫描：散装 .md 不加载，SKILL.md 目录保留，
    base_dir 指向各自的 .agents 目录（对齐 TS 逐组 metadata）。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    agents_skills = tmp_path / ".agents" / "skills"
    agents_skills.mkdir(parents=True)
    (agents_skills / "README.md").write_text(
        "---\nname: readme\ndescription: loose\n---", encoding="utf-8"
    )
    skill_dir = agents_skills / "foo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: foo\ndescription: d\n---", encoding="utf-8"
    )

    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(
        "nova_harness.core.package.resolve.resolver.collect_ancestor_agents_skills_dirs",
        lambda start_dir, stop_at_git_root=True: [str(agents_skills.resolve())],
    )

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    agents_hits = [r for r in result.skills if ".agents" in r.path]
    assert [Path(r.path).name for r in agents_hits] == ["foo"]
    assert agents_hits[0].metadata.base_dir == str(agents_skills.parent.resolve())
    assert agents_hits[0].enabled is True


@pytest.mark.asyncio
async def test_auto_discovery_prompts_flat_only(resolver_dirs: dict[str, Path]) -> None:
    """顶层 prompts 自动发现只收当前层级 .md，嵌套子目录不收（对齐 TS）。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    _write_prompt(cwd / ".nova" / "backend" / "prompts" / "top.md")
    nested = cwd / ".nova" / "backend" / "prompts" / "nested"
    nested.mkdir()
    _write_prompt(nested / "inner.md")

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    names = {Path(r.path).name for r in result.prompts}
    assert names == {"top.md"}


@pytest.mark.asyncio
async def test_direct_entries_directory_expands_by_resource_type(
    resolver_dirs: dict[str, Path],
) -> None:
    """settings 直接条目指向目录时按资源类型递归展开。

    回归测试：该路径曾经引用未导入的 ``RESOURCE_DISCOVERY``，直接 NameError。
    """
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]

    # user scope：agent_dir 下的扩展容器目录（含根级 .py 与子目录扩展）
    bundle = agent_dir / "ext-bundle"
    bundle.mkdir()
    _write_ext_root(bundle / "single.py")
    _write_ext_file(bundle / "pkg_ext")

    # project scope：.nova 下的 skill 容器目录
    skills_dir = cwd / ".nova" / "skill-bundle"
    skill_root = skills_dir / "my-skill"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\nname: my-skill\ndescription: d\n---")

    sm = _FakeSettingsManager(
        Settings(extensions=["ext-bundle"]),
        Settings(skills=["skill-bundle"]),
    )
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    ext_paths = {Path(r.path).name for r in result.extensions if "ext-bundle" in r.path}
    assert ext_paths == {"single.py", "pkg_ext"}
    assert all(r.enabled for r in result.extensions if "ext-bundle" in r.path)

    skill_paths = [r for r in result.skills if "skill-bundle" in r.path]
    assert len(skill_paths) == 1
    assert skill_paths[0].enabled is True


# =============================================================================
# personas 资源类目（persona 升格）
# =============================================================================


def _write_personas_root(path: Path) -> None:
    """写一个 personas 根目录（含嵌套人格文件）。"""
    (path / "coding").mkdir(parents=True)
    (path / "coding" / "core.md").write_text("核心人格", encoding="utf-8")
    (path / "subagents").mkdir(parents=True)
    (path / "subagents" / "scout.md").write_text("侦察人格", encoding="utf-8")


@pytest.mark.asyncio
async def test_personas_package_manifest_category(
    resolver_dirs: dict[str, Path],
) -> None:
    """包 manifest 声明 personas 类目：personas 根目录作为包资源解析。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg_dir = resolver_dirs["cwd"].parent / "pkg-personas"
    pkg_dir.mkdir()
    _write_personas_root(pkg_dir / "personas")
    (pkg_dir / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "pkg-personas"\nversion = "1.0.0"\n'
        '\n[tool.nova]\npersonas = ["./personas/"]\n'
    )

    sm = _FakeSettingsManager(Settings(packages=[str(pkg_dir)]), Settings())
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    # 收集粒度 = personas 根目录（命名根随条目走，逐文件展开归 loader）
    assert len(result.personas) == 1
    entry = result.personas[0]
    assert entry.path == str((pkg_dir / "personas").resolve())
    assert entry.enabled is True
    assert entry.metadata.origin == SourceOrigin.PACKAGE


@pytest.mark.asyncio
async def test_personas_package_filter_disabled(resolver_dirs: dict[str, Path]) -> None:
    """包级 filter：personas = [] 显式禁用该类目（条目保留但 enabled=False）。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    pkg_dir = resolver_dirs["cwd"].parent / "pkg-personas-off"
    pkg_dir.mkdir()
    _write_personas_root(pkg_dir / "personas")
    (pkg_dir / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "pkg-personas-off"\nversion = "1.0.0"\n'
    )

    sm = _FakeSettingsManager(
        Settings(packages=[{"source": str(pkg_dir), "personas": []}]),
        Settings(),
    )
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    assert len(result.personas) == 1
    assert result.personas[0].enabled is False


@pytest.mark.asyncio
async def test_personas_settings_direct_entries(resolver_dirs: dict[str, Path]) -> None:
    """settings personas 数组（路径 + pattern，同 skills 形态）：目录条目解析。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    _write_personas_root(agent_dir / "my-personas")
    _write_personas_root(cwd / ".nova" / "team-personas")

    sm = _FakeSettingsManager(
        Settings(personas=["my-personas"]),
        Settings(personas=["team-personas"]),
    )
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    assert len(result.personas) == 2
    by_scope = {r.metadata.scope: r for r in result.personas}
    assert by_scope[SourceScope.USER].path.endswith("my-personas")
    assert by_scope[SourceScope.USER].metadata.source == "local"
    assert by_scope[SourceScope.PROJECT].path.endswith("team-personas")


@pytest.mark.asyncio
async def test_personas_auto_discovery(resolver_dirs: dict[str, Path]) -> None:
    """自动发现：user 级 ~/.nova/agent/backend/personas/ 与项目级 .nova/backend/personas/。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    _write_personas_root(agent_dir / "backend" / "personas")
    _write_personas_root(cwd / ".nova" / "backend" / "personas")

    sm = _FakeSettingsManager(Settings(), Settings())
    resolver = PackageResolver(
        cwd=str(cwd), agent_dir=str(agent_dir), settings_manager=sm
    )
    result = await resolver.resolve()

    assert len(result.personas) == 2
    by_scope = {r.metadata.scope: r for r in result.personas}
    assert by_scope[SourceScope.USER].metadata.source == "auto"
    assert by_scope[SourceScope.PROJECT].metadata.source == "auto"
    assert by_scope[SourceScope.PROJECT].path == str(
        (cwd / ".nova" / "backend" / "personas").resolve()
    )


@pytest.mark.asyncio
async def test_personas_project_untrusted_not_collected(
    resolver_dirs: dict[str, Path],
) -> None:
    """项目不被信任：project 级 personas（自动发现 + settings 条目）整体不收。"""
    cwd = resolver_dirs["cwd"]
    agent_dir = resolver_dirs["agent_dir"]
    _write_personas_root(agent_dir / "backend" / "personas")
    _write_personas_root(cwd / ".nova" / "backend" / "personas")
    _write_personas_root(cwd / ".nova" / "team-personas")

    sm = _FakeSettingsManager(Settings(), Settings(personas=["team-personas"]))
    sm.set_project_trusted(False)
    resolver = PackageResolver(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        settings_manager=sm,
        project_trusted=False,
    )
    result = await resolver.resolve()

    assert len(result.personas) == 1
    assert result.personas[0].metadata.scope == SourceScope.USER
