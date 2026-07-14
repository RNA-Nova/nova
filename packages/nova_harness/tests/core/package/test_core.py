"""Tests for package_manager/core.py under the whole-package install model.

Packages are installed intact under ``<agent_dir>/packages/{git,local}/`` and
never copied out into ``agents/`` or ``tools/`` subdirectories.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import MagicMock, patch

import pytest

from nova_harness.core.package import PackageManager
from nova_harness.core.package.installer import PackageInstaller


def _write_pyproject_manifest(
    path: Path,
    name: str,
    version: str,
    description: str = "",
    dependencies: Optional[Dict[str, str]] = None,
    **nova_fields,
) -> None:
    """Write a minimal pyproject.toml with [tool.nova] section for tests."""
    lines = [
        "[build-system]",
        'requires = ["poetry-core>=1.0.0"]',
        'build-backend = "poetry.core.masonry.api"',
        "",
        "[tool.poetry]",
        f'name = "{name}"',
        f'version = "{version}"',
    ]
    if description:
        lines.append(f'description = "{description}"')
    lines.append('authors = ["nova"]')
    if dependencies:
        lines.append("")
        lines.append("[tool.poetry.dependencies]")
        for dep_name, dep_spec in dependencies.items():
            lines.append(f'{dep_name} = "{dep_spec}"')
    lines.append("")
    if nova_fields:
        lines.append("[tool.nova]")
        for key, value in nova_fields.items():
            if isinstance(value, list):
                items = [f'"{v}"' for v in value]
                lines.append(f"{key} = [{', '.join(items)}]")
            elif isinstance(value, dict):
                items = [f'{k} = "{v}"' for k, v in value.items()]
                lines.append(f"{key} = {{ {', '.join(items)} }}")
            elif isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            else:
                lines.append(f'{key} = "{value}"')
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_bundle_pkg(path: Path, with_manifest: bool = True) -> None:
    """Create a minimal package containing an agent and a tool."""
    agents = path / "agents" / "coding_agent"
    agents.mkdir(parents=True)
    (agents / "description.md").write_text("coding agent")
    (agents / "sections").mkdir()
    (agents / "sections" / "role.md").write_text("role")

    tool = path / "tools" / "bash"
    tool.mkdir(parents=True)
    (tool / "schema.json").write_text(
        json.dumps(
            {
                "name": "bash",
                "description": "run shell commands",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool / "executor.py").write_text(
        "class ToolExecutor:\n    async def execute(self, *a, **k):\n        pass\n"
    )

    if with_manifest:
        _write_pyproject_manifest(
            path / "pyproject.toml",
            name="test-bundle",
            version="1.0.0",
            description="Test bundle",
            agents=["./agents/coding_agent"],
            tools=["./tools/bash"],
        )


def _make_agent_pkg(path: Path, name: str = "my-agent") -> None:
    """Create a single agent config package."""
    path.mkdir(parents=True, exist_ok=True)
    agent_dir = path / "agents" / name
    agent_dir.mkdir(parents=True)
    _write_pyproject_manifest(
        path / "pyproject.toml",
        name=name,
        version="0.5.0",
        description="My agent",
        agents=[f"./agents/{name}"],
    )
    (agent_dir / "description.md").write_text("agent")
    (agent_dir / "sections").mkdir()
    (agent_dir / "sections" / "role.md").write_text("role")


def _make_tool_pkg(path: Path, dependencies: Optional[Dict[str, str]] = None) -> None:
    """Create a single tool package."""
    path.mkdir(parents=True)
    tool_dir = path / "tools" / "my-tool"
    tool_dir.mkdir(parents=True)
    _write_pyproject_manifest(
        path / "pyproject.toml",
        name="my-tool",
        version="0.5.0",
        description="My tool",
        dependencies=dependencies,
        tools=["./tools/my-tool"],
    )
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": "my-tool",
                "description": "does thing",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text(
        "class ToolExecutor:\n    async def execute(self, *a, **k):\n        pass\n"
    )


def _make_skill_pkg(path: Path) -> None:
    """Create a single skill package."""
    path.mkdir(parents=True)
    skill_dir = path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    _write_pyproject_manifest(
        path / "pyproject.toml",
        name="my-skill",
        version="0.5.0",
        description="My skill",
        skills=["./skills/my-skill"],
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A useful skill\n---\n\n# Skill\n",
        encoding="utf-8",
    )


def _make_extension_pkg(path: Path) -> None:
    """Create a single extension package."""
    path.mkdir(parents=True)
    ext_dir = path / "extensions" / "my-extension"
    ext_dir.mkdir(parents=True)
    _write_pyproject_manifest(
        path / "pyproject.toml",
        name="my-extension",
        version="0.5.0",
        description="My extension",
        extensions=["./extensions/my-extension"],
    )
    (ext_dir / "extension.py").write_text("# extension entry point\n")


@pytest.fixture
def pm(tmp_path):
    return PackageManager(
        agent_dir=str(tmp_path / "agent"),
        cwd=str(tmp_path),
        project_trusted=True,
    )


@pytest.fixture(autouse=True)
def no_dependency_install():
    """Prevent tests from running real pip/uv commands."""
    with patch("nova_harness.core.package.installer.install_dependencies"):
        with patch("nova_harness.core.package.installer.install_package"):
            with patch("nova_harness.core.package.installer.uninstall_package"):
                with patch(
                    "nova_harness.core.package.manager.uninstall_package"
                ) as mock:
                    yield mock


# ---------------------------------------------------------------------------
# Whole-package install
# ---------------------------------------------------------------------------


def test_install_bundle_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "test-bundle"
    assert meta.source == str(pkg.resolve())
    assert Path(meta.install_path).exists()
    assert (
        Path(meta.install_path) / "agents" / "coding_agent" / "description.md"
    ).exists()
    assert (Path(meta.install_path) / "tools" / "bash" / "schema.json").exists()

    # Resources must NOT be copied out to legacy type directories.
    # agents/ may be pre-created as an empty discovery entry; only tools/ is
    # intentionally not created.
    assert not (pm.agent_dir / "tools").exists()


def test_is_package_resolvable_for_git_does_not_clone(pm, tmp_path, monkeypatch):
    """对未安装的 git 源，PackageCoordinator 只检查本地缓存是否存在，不触发 clone。"""
    from nova_harness.core.package.source import parse_source
    from nova_harness.core.types.package_manager import SourceScope

    source = "git:github.com/user/repo"
    source_obj = parse_source(source)

    install_path = pm._user_installer.git_root / source_obj.host / source_obj.repo_path
    assert not (install_path / ".git").exists()

    def fake_resolve(_):
        raise RuntimeError("git resolve should not be called for resolvability check")

    monkeypatch.setattr(pm._user_installer.source_resolver, "resolve", fake_resolve)

    result = pm._is_package_resolvable(source, SourceScope.USER)
    assert result is False


def test_install_creates_discovery_directories(pm, tmp_path):
    pkg = tmp_path / "legacy-bundle"
    _make_bundle_pkg(pkg, with_manifest=False)

    # 包含 tools/extensions 的包必须声明 Python 包名，否则无法 import helper 模块。
    with pytest.raises(ValueError, match="declare a Python package name"):
        pm.install(str(pkg))


def test_install_creates_top_level_resource_directories(pm, tmp_path):
    """安装任意包时应在 agent_dir 下创建顶层资源发现目录（tools 除外）。"""
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    pm.install(str(pkg))

    assert (pm.agent_dir / "agents").exists()
    assert (pm.agent_dir / "prompts").exists()
    assert (pm.agent_dir / "skills").exists()
    assert (pm.agent_dir / "extensions").exists()
    assert (pm.agent_dir / "themes").exists()
    assert not (pm.agent_dir / "tools").exists()
    pkg = tmp_path / "legacy-agent-bundle"
    agents = pkg / "agents" / "coding_agent"
    agents.mkdir(parents=True)
    (agents / "description.md").write_text("coding agent")

    meta = pm.install(str(pkg))

    assert meta.name == "legacy-agent-bundle"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "agents" / "coding_agent").exists()


def test_install_agent_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "my-agent"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "agents" / "my-agent" / "description.md").exists()

    # tools/ is intentionally not created; agents/ may be pre-created empty.
    assert not (pm.agent_dir / "tools").exists()


def test_install_tool_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "my-tool"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "tools" / "my-tool" / "schema.json").exists()
    assert not (pm.agent_dir / "tools").exists()


def test_install_skill_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "my-skill"
    _make_skill_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "my-skill"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "skills" / "my-skill" / "SKILL.md").exists()


def test_install_extension_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "my-extension"
    _make_extension_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "my-extension"
    assert Path(meta.install_path).exists()
    assert (
        Path(meta.install_path) / "extensions" / "my-extension" / "extension.py"
    ).exists()


def test_install_extension_triggers_python_package_install(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-extension"
    _make_extension_pkg(pkg)

    with patch("nova_harness.core.package.installer.install_package") as mock_pkg:
        meta = pm.install(str(pkg))

    assert meta.package_name == "my-extension"
    # 普通安装现在从 Nova 管理目录的副本执行 pip install。
    expected_path = Path(meta.install_path).resolve()
    mock_pkg.assert_called_once_with(str(expected_path), editable=False)


def test_install_without_name_override(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install(str(pkg))
    assert meta.name == "my-tool"
    assert Path(meta.install_path).name == "my-tool"
    assert (Path(meta.install_path) / "tools" / "my-tool" / "schema.json").exists()


def test_install_and_persist_without_name_override(pm, tmp_path):
    """安装并持久化后，list/info/uninstall/update 都使用 manifest 名称。"""
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install_and_persist(str(pkg))
    assert meta.name == "my-tool"
    install_path = Path(meta.install_path)

    # settings 中应记录相对 source 字符串（name override 已删除）
    sources = pm.settings_manager.get_global_settings().packages
    assert len(sources) == 1
    assert sources[0] == "path:../my-tool"

    # list / info 使用 manifest 名称
    listed = pm.list()
    assert len(listed) == 1
    assert listed[0].name == "my-tool"
    assert listed[0].install_path == str(install_path)

    info = pm.info("my-tool")
    assert info is not None
    assert info.name == "my-tool"
    assert info.install_path == str(install_path)

    # uninstall 按 manifest 名称工作
    ok = pm.uninstall("my-tool")
    assert ok.removed is True
    assert not install_path.exists()


# ---------------------------------------------------------------------------
# Persistence, list, info, uninstall, update
# ---------------------------------------------------------------------------


def test_install_and_persist_records_source(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    meta = pm.install_and_persist(str(pkg))

    assert meta.name == "test-bundle"
    sources = pm.settings_manager.get_global_settings().packages
    assert "path:../test-bundle" in sources


def test_add_source_normalizes_before_identity_comparison(pm, tmp_path):
    """同一本地路径用绝对/相对/path:等不同写法不应产生重复 settings 条目。"""
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    pm.install_and_persist(str(pkg))
    # 再次用相对路径+path:前缀安装，应视为同一包并更新条目而非新增。
    # pm.cwd 已隔离到 tmp_path，因此相对路径基于 tmp_path 计算。
    rel_source = f"path:{os.path.relpath(str(pkg), str(pm.cwd))}"
    pm.install_and_persist(rel_source)

    sources = pm.settings_manager.get_global_settings().packages
    assert len(sources) == 1


def test_uninstall_removes_package_directory_and_settings(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    meta = pm.install_and_persist(str(pkg))
    install_path = Path(meta.install_path)

    ok = pm.uninstall("test-bundle")

    assert ok.removed is True
    assert not install_path.exists()
    assert str(pkg.resolve()) not in pm.settings_manager.get_global_settings().packages
    assert pm.info("test-bundle") is None


async def test_update_reinstalls_package(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install_and_persist(str(pkg))

    (pkg / "agents" / "coding_agent" / "description.md").write_text("updated")

    updated = await pm.update("test-bundle")
    assert len(updated) == 1
    assert updated[0].name == "test-bundle"
    assert (
        Path(updated[0].install_path) / "agents" / "coding_agent" / "description.md"
    ).read_text() == "updated"


def test_list_reads_from_settings(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install_and_persist(str(pkg))

    pkgs = pm.list()
    names = {p.name for p in pkgs}
    assert "test-bundle" in names


def test_info_returns_metadata(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    pm.install_and_persist(str(pkg))

    meta = pm.info("my-agent")
    assert meta is not None
    assert meta.name == "my-agent"
    assert meta.version == "0.5.0"


# ---------------------------------------------------------------------------
# Dry run, dependencies, validation
# ---------------------------------------------------------------------------


def test_dry_run_does_not_copy(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    meta = pm.install(str(pkg), dry_run=True)

    assert meta.name == "test-bundle"
    assert not Path(meta.install_path).exists()


def test_no_deps_skips_dependency_install(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    (pkg / "requirements.txt").write_text("requests>=2.0\n", encoding="utf-8")

    with patch("nova_harness.core.package.installer.install_dependencies") as mock:
        pm.install(str(pkg), no_deps=True)
        mock.assert_not_called()


def test_dependency_install_called(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    (pkg / "requirements.txt").write_text("requests>=2.0\n", encoding="utf-8")

    with patch(
        "nova_harness.core.package.installer.check_dependency_conflicts"
    ) as mock:
        pm.install(str(pkg))
        mock.assert_called_once()


def test_validate_bundle(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_agent(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_tool(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_skill(pm, tmp_path):
    pkg = tmp_path / "my-skill"
    _make_skill_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_extension(pm, tmp_path):
    pkg = tmp_path / "my-extension"
    _make_extension_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_empty_package(pm, tmp_path):
    pkg = tmp_path / "empty"
    pkg.mkdir()
    (pkg / "pyproject.toml").write_text('[tool.poetry]\nname = "empty"\n')
    issues = pm.validate(str(pkg))
    assert any(
        "agents" in i or "tools" in i or "skills" in i or "extensions" in i
        for i in issues
    )


# ---------------------------------------------------------------------------
# Editable (reference) installs
# ---------------------------------------------------------------------------


def test_install_editable_creates_symlink_in_packages_path(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    meta = pm.install(str(pkg), editable=True)

    assert meta.name == "my-agent"
    symlink_path = pm.path_root / "my-agent"
    assert symlink_path.is_symlink()
    assert symlink_path.resolve() == pkg.resolve()
    assert meta.install_path == str(symlink_path)


def test_install_and_persist_editable_records_source(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    meta = pm.install_and_persist(str(pkg), editable=True)

    assert meta.source == str(pkg)
    sources = pm.settings_manager.get_global_settings().packages
    assert {"source": "path:../test-bundle", "editable": True} in sources


def test_uninstall_editable_does_not_remove_source(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    pm.install_and_persist(str(pkg), editable=True)

    ok = pm.uninstall("my-agent")

    assert ok.removed is True
    assert pkg.exists()  # original directory must remain
    assert pm.settings_manager.get_global_settings().packages == []


async def test_update_editable_reloads_metadata(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    pm.install_and_persist(str(pkg), editable=True)

    # Mutate source after install
    (pkg / "agents" / "my-agent" / "description.md").write_text("updated")

    updated = await pm.update("my-agent")
    assert len(updated) == 1
    assert updated[0].name == "my-agent"
    symlink_path = pm.path_root / "my-agent"
    assert symlink_path.is_symlink()
    assert updated[0].install_path == str(symlink_path)


def test_install_editable_installs_deps_and_editable_package_for_tools(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg, dependencies={"requests": ">=2.0"})

    with patch(
        "nova_harness.core.package.installer.check_dependency_conflicts",
        return_value="",
    ):
        with patch(
            "nova_harness.core.package.installer.install_dependencies"
        ) as mock_deps:
            with patch(
                "nova_harness.core.package.installer.install_package"
            ) as mock_pkg:
                meta = pm.install(str(pkg), editable=True)

    assert meta.name == "my-tool"
    symlink_path = pm.path_root / "my-tool"
    assert symlink_path.is_symlink()
    assert symlink_path.resolve() == pkg.resolve()
    assert meta.install_path == str(symlink_path)
    assert meta.package_name == "my-tool"
    mock_deps.assert_called_once()
    call_args = mock_deps.call_args
    assert "requests>=2.0" in call_args[0][0]
    mock_pkg.assert_called_once_with(str(pkg.resolve()), editable=True)


def test_install_editable_skips_package_install_for_agent_only(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    with patch("nova_harness.core.package.installer.install_package") as mock_pkg:
        meta = pm.install(str(pkg), editable=True)

    assert meta.package_name == ""
    mock_pkg.assert_not_called()


def test_install_editable_respects_no_deps(pm, tmp_path, no_dependency_install):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    with patch("nova_harness.core.package.installer.install_dependencies") as mock_deps:
        with patch("nova_harness.core.package.installer.install_package"):
            pm.install(str(pkg), editable=True, no_deps=True)

    mock_deps.assert_not_called()


def test_install_editable_extension_triggers_python_package_install(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-extension"
    _make_extension_pkg(pkg)

    with patch("nova_harness.core.package.installer.install_package") as mock_pkg:
        meta = pm.install(str(pkg), editable=True)

    assert meta.package_name == "my-extension"
    mock_pkg.assert_called_once_with(str(pkg.resolve()), editable=True)


def test_uninstall_editable_uninstalls_python_package(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    pm.install_and_persist(str(pkg), editable=True)

    ok = pm.uninstall("my-tool")

    assert ok.removed is True
    assert pkg.exists()  # original directory must remain
    assert pm.settings_manager.get_global_settings().packages == []
    no_dependency_install.assert_called_once_with("my-tool")


# ---------------------------------------------------------------------------
# Git source identity and ref switching
# ---------------------------------------------------------------------------


def test_install_git_source(pm, tmp_path):
    pkg = tmp_path / "git-pkg"
    _make_agent_pkg(pkg)

    def fake_resolve_git(source):
        return str(pkg)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        meta = pm.install("git:github.com/user/repo@main")

    assert meta.name == "my-agent"
    assert meta.source == "git:github.com/user/repo@main"
    assert meta.install_path == str(pm.git_root / "github.com" / "user" / "repo")
    assert Path(meta.install_path).exists()


def test_install_git_source_no_self_copy(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"
    install_path.mkdir(parents=True)
    _make_agent_pkg(install_path)

    def fake_resolve_git(source):
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        meta = pm.install("git:github.com/user/repo@main")

    assert meta.install_path == str(install_path)
    # The resolver already placed content at install_path; install must not
    # destroy it by copying the directory onto itself.
    assert (install_path / "agents" / "my-agent" / "description.md").exists()


def test_install_git_switches_ref(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"

    def fake_resolve_git(source):
        ref = source.ref
        install_path.mkdir(parents=True, exist_ok=True)
        agent_dir = install_path / "agents" / "my-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "description.md").write_text(f"ref={ref}")
        _write_pyproject_manifest(
            install_path / "pyproject.toml",
            name="my-agent",
            version="0.5.0",
            agents=["./agents/my-agent"],
        )
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        pm.install("git:github.com/user/repo@main")
        assert (
            install_path / "agents" / "my-agent" / "description.md"
        ).read_text() == "ref=main"
        pm.install("git:github.com/user/repo@v1.0")
        assert (
            install_path / "agents" / "my-agent" / "description.md"
        ).read_text() == "ref=v1.0"


def test_settings_dedup_git_same_repo_different_ref(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"
    install_path.mkdir(parents=True)
    _make_agent_pkg(install_path)

    def fake_resolve_git(source):
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        pm.install_and_persist("git:github.com/user/repo@main")
        pm.install_and_persist("git:github.com/user/repo@v1.0")

    sources = pm.settings_manager.get_global_settings().packages
    assert len(sources) == 1
    assert sources[0] == "git:github.com/user/repo@v1.0"


def test_uninstall_git_by_identity_different_ref(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"
    install_path.mkdir(parents=True)
    _make_agent_pkg(install_path)

    def fake_resolve_git(source):
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        pm.install_and_persist("git:github.com/user/repo@main")
        ok = pm.uninstall("my-agent")

    assert ok.removed is True
    assert not install_path.exists()
    assert pm.info("my-agent") is None


def test_install_editable_rejects_git_source(pm, tmp_path):
    """editable 标志只对 path 源有意义，git 源应直接拒绝。"""
    pkg = tmp_path / "git-pkg"
    _make_agent_pkg(pkg)

    def fake_resolve_git(source):
        return str(pkg)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        with pytest.raises(
            ValueError, match="Editable mode only supports path sources"
        ):
            pm.install("git:github.com/user/repo@main", editable=True)


def test_find_by_name_raises_ambiguous_error():
    """同一名称匹配到多个包时应抛出 AmbiguousPackageNameError。"""
    from nova_harness.core.types.package_manager import (
        AmbiguousPackageNameError,
        PackageMetadata,
    )

    installer = MagicMock()
    installer.list.return_value = [
        PackageMetadata(
            name="shared",
            source="path:/a",
            install_path="/a",
            version="1.0.0",
            description="",
            installed_at="",
        ),
        PackageMetadata(
            name="shared",
            source="path:/b",
            install_path="/b",
            version="1.0.0",
            description="",
            installed_at="",
        ),
    ]

    with patch.object(installer, "list", installer.list):
        with pytest.raises(AmbiguousPackageNameError, match="shared"):
            PackageInstaller.find_by_name(installer, "shared")
