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
from nova_harness.core.package.install.installer import PackageInstaller


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
    agents_dir = path / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "coding_agent.yaml").write_text(
        "description: coding agent\npersona: [personas/role.md]\n"
    )
    personas_dir = agents_dir / "personas"
    personas_dir.mkdir()
    (personas_dir / "role.md").write_text("role")

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
        "class Tool:\n    async def execute(self, *a, **k):\n        pass\n"
    )

    if with_manifest:
        _write_pyproject_manifest(
            path / "pyproject.toml",
            name="test-bundle",
            version="1.0.0",
            description="Test bundle",
            agents=["./agents/coding_agent.yaml"],
            tools=["./tools/bash"],
        )


def _make_agent_pkg(path: Path, name: str = "my-agent") -> None:
    """Create a single agent config package."""
    path.mkdir(parents=True, exist_ok=True)
    agents_dir = path / "agents"
    agents_dir.mkdir(parents=True)
    _write_pyproject_manifest(
        path / "pyproject.toml",
        name=name,
        version="0.5.0",
        description="My agent",
        agents=["./agents"],
    )
    (agents_dir / f"{name}.yaml").write_text(
        "description: agent\npersona: [personas/role.md]\n"
    )
    personas_dir = agents_dir / "personas"
    personas_dir.mkdir()
    (personas_dir / "role.md").write_text("role")


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
        "class Tool:\n    async def execute(self, *a, **k):\n        pass\n"
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
    with patch("nova_harness.core.package.install.installer.install_dependencies"):
        with patch("nova_harness.core.package.install.installer.install_package"):
            with patch("nova_harness.core.package.install.installer.uninstall_package"):
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
    assert (Path(meta.install_path) / "agents" / "coding_agent.yaml").exists()
    assert (Path(meta.install_path) / "tools" / "bash" / "schema.json").exists()

    # Resources must NOT be copied out to legacy type directories.
    # agents/ may be pre-created as an empty discovery entry; only tools/ is
    # intentionally not created.
    assert not (pm.agent_dir / "tools").exists()


def test_is_package_resolvable_for_git_does_not_clone(pm, tmp_path, monkeypatch):
    """对未安装的 git 源，PackageCoordinator 只检查本地缓存是否存在，不触发 clone。"""
    from nova_harness.core.package.source.spec import parse_source
    from nova_harness.core.types.package import SourceScope

    source = "git:github.com/user/repo"
    source_obj = parse_source(source)

    install_path = pm._user_installer.git_root / source_obj.host / source_obj.repo_path
    assert not (install_path / ".git").exists()

    def fake_resolve(_):
        raise RuntimeError("git resolve should not be called for resolvability check")

    monkeypatch.setattr(pm._user_installer.source_resolver, "resolve", fake_resolve)

    result = pm._is_package_resolvable(source, SourceScope.USER)
    assert result is False


def test_is_package_resolvable_git_requires_dist_info(pm, tmp_path):
    """git 缓存存在但无 dist-info（validate clone / 幽灵副本）→ 判未安装，触发自愈重装。"""
    from nova_harness.core.package.install.store import write_dist_info
    from nova_harness.core.package.source.spec import parse_source
    from nova_harness.core.types.package import SourceScope

    source = "git:github.com/user/repo"
    source_obj = parse_source(source)
    install_path = pm.git_root / "github.com" / "user" / "repo"
    install_path.mkdir(parents=True)
    (install_path / ".git").mkdir()

    # 只有缓存、没有安装完成标志 → 不可解析。
    assert pm._is_package_resolvable(source, SourceScope.USER) is False

    # 写入 dist-info（安装完成标志）后视为可解析。
    write_dist_info(str(install_path), source_obj, "", editable=False, package_name="")
    assert pm._is_package_resolvable(source, SourceScope.USER) is True


def test_is_package_resolvable_path_requires_install_copy(pm, tmp_path):
    """path 源：原源在但从未安装（无副本）→ 判未安装，触发自动安装补齐。"""
    from nova_harness.core.types.package import SourceScope

    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    # 原源存在但无安装副本 → 不可解析（旧口径会误判为可解析并原地裸用）。
    assert pm._is_package_resolvable(str(pkg), SourceScope.USER) is False

    pm.install(str(pkg))
    assert pm._is_package_resolvable(str(pkg), SourceScope.USER) is True


def test_is_package_resolvable_path_copy_survives_missing_original(pm, tmp_path):
    """path 源：原源删除后无法重装，副本存在即视为可解析（避免误杀可用副本）。"""
    import shutil

    from nova_harness.core.types.package import SourceScope

    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    pm.install(str(pkg))
    shutil.rmtree(pkg)

    assert pm._is_package_resolvable(str(pkg), SourceScope.USER) is True


def test_install_tools_package_without_python_structure(pm, tmp_path):
    """tools 包没有 Python 包结构（无 pyproject）也可安装：资源照常，但不自安装。

    executor 自包含（不 import 包内模块）时，无包结构是合法形态。
    """
    pkg = tmp_path / "legacy-bundle"
    _make_bundle_pkg(pkg, with_manifest=False)

    meta = pm.install(str(pkg))

    assert meta.name == "legacy-bundle"
    assert meta.package_name == ""
    assert (Path(meta.install_path) / "tools" / "bash" / "executor.py").exists()


def test_install_agent_directory_package(pm, tmp_path):
    """仅含 agents/ 目录（顶层 yaml 组合声明）的无 manifest 包可安装且保持包体完整。"""
    pkg = tmp_path / "legacy-agent-bundle"
    agents_dir = pkg / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "coding_agent.yaml").write_text("description: coding agent\n")

    meta = pm.install(str(pkg))

    assert meta.name == "legacy-agent-bundle"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "agents" / "coding_agent.yaml").exists()


def test_install_named_package_without_build_system_not_self_installed(pm, tmp_path):
    """声明了 name 但无 build-system 的包：正常安装资源，但不自安装 Python 包。

    自安装边界是"是否为可安装 Python 包"（name + build-system），
    缺一即跳过自安装而不是报错。
    """
    pkg = tmp_path / "no-build-system"
    tool_dir = pkg / "tools" / "t"
    tool_dir.mkdir(parents=True)
    (pkg / "pyproject.toml").write_text(
        '[project]\nname = "no-build-system"\nversion = "0.1.0"\n'
        '\n[tool.nova]\ntools = ["./tools/t"]\n',
        encoding="utf-8",
    )
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": "t",
                "description": "t",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text("class Tool:\n    pass\n")

    meta = pm.install(str(pkg))

    assert meta.name == "no-build-system"
    assert meta.package_name == ""


def test_reinstall_same_name_uninstalls_orphan_python_package(pm, tmp_path):
    """同名覆盖安装时，旧包携带的 Python 包应被卸载，避免成为环境孤儿。

    v1 含 tools（package_name="my-tool"）；v2 同名但只含 skills 且不再是
    可安装 Python 包（无 build-system）——旧的 "my-tool" 必须从环境中移除。
    """
    pkg_v1 = tmp_path / "pkg-v1"
    _make_tool_pkg(pkg_v1)
    pm.install(str(pkg_v1))

    pkg_v2 = tmp_path / "pkg-v2"
    skill_dir = pkg_v2 / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    # 无 build-system：按自安装边界（name + build-system），v2 不再自安装。
    (pkg_v2 / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "my-tool"\nversion = "0.6.0"\n'
        '\n[tool.nova]\nskills = ["./skills/my-skill"]\n',
        encoding="utf-8",
    )
    (skill_dir / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: s\n---\n", encoding="utf-8"
    )

    with patch(
        "nova_harness.core.package.install.installer.uninstall_package"
    ) as mock_uninstall:
        pm.install(str(pkg_v2))

    mock_uninstall.assert_called_with("my-tool")


def test_install_agent_keeps_whole_package(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "my-agent"
    assert Path(meta.install_path).exists()
    assert (Path(meta.install_path) / "agents" / "my-agent.yaml").exists()

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

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
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


def test_uninstall_reports_scope_messages(pm, tmp_path):
    """跨 scope 卸载：messages 记录每个 scope 的处置明细。"""
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install_and_persist(str(pkg))
    pm.install_and_persist(str(pkg), local=True)

    result = pm.uninstall("test-bundle")

    assert result.removed is True
    assert "Removed from project scope." in result.messages
    assert "Removed from user scope." in result.messages
    # 自安装边界内的包（build-system + name） refcount 归零后卸载底层 Python 包
    assert "Uninstalled Python package 'test-bundle'." in result.messages


async def test_update_reinstalls_package(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install_and_persist(str(pkg))

    (pkg / "agents" / "coding_agent.yaml").write_text("updated")

    updated = await pm.update("test-bundle")
    assert len(updated) == 1
    assert updated[0].name == "test-bundle"
    assert (
        Path(updated[0].install_path) / "agents" / "coding_agent.yaml"
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

    with patch(
        "nova_harness.core.package.install.installer.install_dependencies"
    ) as mock:
        pm.install(str(pkg), no_deps=True)
        mock.assert_not_called()


def test_dependency_install_called(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    (pkg / "requirements.txt").write_text("requests>=2.0\n", encoding="utf-8")

    with patch(
        "nova_harness.core.package.install.installer.check_dependency_conflicts"
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
    (pkg / "agents" / "my-agent.yaml").write_text("updated")

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
        "nova_harness.core.package.install.installer.check_dependency_conflicts",
        return_value="",
    ):
        with patch(
            "nova_harness.core.package.install.installer.install_dependencies"
        ) as mock_deps:
            with patch(
                "nova_harness.core.package.install.installer.install_package"
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


def test_install_editable_self_installs_when_python_structure_present(
    pm, tmp_path, no_dependency_install
):
    """有完整 Python 包结构（name + build-system）的包即使只含 agents 也自安装。

    自安装边界只看"是不是可安装 Python 包"，不看资源类型。
    """
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
        meta = pm.install(str(pkg), editable=True)

    assert meta.package_name == "my-agent"
    mock_pkg.assert_called_once()


def test_install_editable_respects_no_deps(pm, tmp_path, no_dependency_install):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    with patch(
        "nova_harness.core.package.install.installer.install_dependencies"
    ) as mock_deps:
        with patch("nova_harness.core.package.install.installer.install_package"):
            pm.install(str(pkg), editable=True, no_deps=True)

    mock_deps.assert_not_called()


def test_install_editable_extension_triggers_python_package_install(
    pm, tmp_path, no_dependency_install
):
    pkg = tmp_path / "my-extension"
    _make_extension_pkg(pkg)

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
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

    def fake_resolve_git(source, *, update=False):
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

    def fake_resolve_git(source, *, update=False):
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        meta = pm.install("git:github.com/user/repo@main")

    assert meta.install_path == str(install_path)
    # The resolver already placed content at install_path; install must not
    # destroy it by copying the directory onto itself.
    assert (install_path / "agents" / "my-agent.yaml").exists()


def test_install_git_switches_ref(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"

    def fake_resolve_git(source, *, update=False):
        ref = source.ref
        install_path.mkdir(parents=True, exist_ok=True)
        agents_dir = install_path / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        (agents_dir / "my-agent.yaml").write_text(f"description: ref={ref}\n")
        _write_pyproject_manifest(
            install_path / "pyproject.toml",
            name="my-agent",
            version="0.5.0",
            agents=["./agents"],
        )
        return str(install_path)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        pm.install("git:github.com/user/repo@main")
        assert (
            install_path / "agents" / "my-agent.yaml"
        ).read_text() == "description: ref=main\n"
        pm.install("git:github.com/user/repo@v1.0")
        assert (
            install_path / "agents" / "my-agent.yaml"
        ).read_text() == "description: ref=v1.0\n"


def test_settings_dedup_git_same_repo_different_ref(pm, tmp_path):
    install_path = pm.git_root / "github.com" / "user" / "repo"
    install_path.mkdir(parents=True)
    _make_agent_pkg(install_path)

    def fake_resolve_git(source, *, update=False):
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

    def fake_resolve_git(source, *, update=False):
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

    def fake_resolve_git(source, *, update=False):
        return str(pkg)

    with patch.object(pm.source_resolver, "_resolve_git", side_effect=fake_resolve_git):
        with pytest.raises(
            ValueError, match="Editable mode only supports path sources"
        ):
            pm.install("git:github.com/user/repo@main", editable=True)


def test_find_by_name_raises_ambiguous_error():
    """同一名称匹配到多个包时应抛出 AmbiguousPackageNameError。"""
    from nova_harness.core.types.package import (
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


def test_install_setup_cfg_project_self_installs(pm, tmp_path, no_dependency_install):
    """setup.py + setup.cfg[metadata].name 的老式 setuptools 项目可自安装。"""
    pkg = tmp_path / "legacy-setup"
    tool_dir = pkg / "tools" / "t"
    tool_dir.mkdir(parents=True)
    (pkg / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    (pkg / "setup.cfg").write_text("[metadata]\nname = legacy-setup\n")
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": "t",
                "description": "t",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text("class Tool:\n    pass\n")

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
        meta = pm.install(str(pkg))

    assert meta.package_name == "legacy-setup"
    mock_pkg.assert_called_once()


def test_install_setup_py_ast_name_self_installs(pm, tmp_path, no_dependency_install):
    """setup.py 中 setup(name=\"...\") 字面量可被 AST 识别并自安装。"""
    pkg = tmp_path / "ast-setup"
    tool_dir = pkg / "tools" / "t"
    tool_dir.mkdir(parents=True)
    (pkg / "setup.py").write_text(
        "from setuptools import setup\nsetup(name='ast-setup', version='1.0')\n"
    )
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": "t",
                "description": "t",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text("class Tool:\n    pass\n")

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
        meta = pm.install(str(pkg))

    assert meta.package_name == "ast-setup"
    mock_pkg.assert_called_once()


def test_install_setup_cfg_alone_not_installable(pm, tmp_path, no_dependency_install):
    """setup.cfg 单独存在（无 setup.py 驱动）不算可安装包，不自安装。"""
    pkg = tmp_path / "cfg-only"
    tool_dir = pkg / "tools" / "t"
    tool_dir.mkdir(parents=True)
    (pkg / "setup.cfg").write_text("[metadata]\nname = cfg-only\n")
    (tool_dir / "schema.json").write_text(
        json.dumps(
            {
                "name": "t",
                "description": "t",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (tool_dir / "executor.py").write_text("class Tool:\n    pass\n")

    with patch(
        "nova_harness.core.package.install.installer.install_package"
    ) as mock_pkg:
        meta = pm.install(str(pkg))

    assert meta.package_name == ""
    mock_pkg.assert_not_called()


def test_install_writes_packages_gitignore(pm, tmp_path):
    """安装时在 packages/ 下写入 .gitignore，防止安装产物被 git 追踪。"""
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    pm.install(str(pkg))

    gitignore = pm._user_installer.packages_dir / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text(encoding="utf-8") == "*\n!.gitignore\n"


def test_install_writes_dist_info(pm, tmp_path):
    """安装时写入 PEP 610 风格的 dist-info（direct_url + package_name + installed_at）。"""
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install(str(pkg))

    dist_dir = Path(meta.install_path).parent / (
        Path(meta.install_path).name + ".dist-info"
    )
    assert dist_dir.is_dir()

    direct_url = json.loads((dist_dir / "direct_url.json").read_text())
    assert direct_url["url"] == Path(str(pkg)).as_uri()
    assert direct_url["dir_info"] == {"editable": False}

    assert (dist_dir / "package_name").read_text().strip() == "my-tool"
    assert (dist_dir / "installed_at").read_text().strip()
    assert meta.installed_at


def test_dist_info_snapshot_wins_over_copy_mutation(pm, tmp_path):
    """dist-info 快照优先：副本 manifest 被篡改后，package_name 仍取安装时记录。

    包内容（显示名）跟随副本变化是合理的；但 Python 分发名映射是安装事实，
    必须保持快照——否则卸载时无法正确清理环境。
    """
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    meta = pm.install(str(pkg))

    # 篡改副本的 pyproject（改名），推导会得到新名字，但快照应保持原名。
    pyproject = Path(meta.install_path) / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace('name = "my-tool"', 'name = "mutated"')
    )

    shown = next(
        p for p in pm._user_installer.list() if p.install_path == meta.install_path
    )
    assert shown.name == "mutated"  # 包内容跟随副本（合理）
    assert shown.package_name == "my-tool"  # 安装事实保持快照


def test_uninstall_removes_dist_info(pm, tmp_path):
    """卸载时连同 dist-info 目录一并删除。"""
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    meta = pm.install(str(pkg))
    dist_dir = Path(meta.install_path).parent / (
        Path(meta.install_path).name + ".dist-info"
    )
    assert dist_dir.is_dir()

    pm.uninstall("my-tool")

    assert not dist_dir.exists()
    assert not Path(meta.install_path).exists()
