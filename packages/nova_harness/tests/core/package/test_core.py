"""Tests for package_manager/core.py."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_harness.core.package import PackageManager


def _write_package_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_bundle_pkg(path: Path, with_manifest: bool = True) -> None:
    """Create a minimal bundle package at *path*."""
    agents = path / "agents" / "coding_agent"
    agents.mkdir(parents=True)
    (agents / "description.md").write_text("coding agent")
    (agents / "setup.md").write_text("setup")
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
        _write_package_json(
            path / "package.json",
            {
                "name": "test-bundle",
                "version": "1.0.0",
                "description": "Test bundle",
                "kind": "bundle",
                "nova": {
                    "agents": ["./agents/coding_agent"],
                    "tools": ["./tools/bash"],
                },
            },
        )


def _make_agent_pkg(path: Path) -> None:
    """Create a single agent config package."""
    path.mkdir(parents=True)
    _write_package_json(
        path / "package.json",
        {
            "name": "my-agent",
            "version": "0.5.0",
            "description": "My agent",
        },
    )
    (path / "description.md").write_text("agent")
    (path / "setup.md").write_text("setup")
    (path / "sections").mkdir()
    (path / "sections" / "role.md").write_text("role")


def _make_tool_pkg(path: Path) -> None:
    path.mkdir(parents=True)
    _write_package_json(
        path / "package.json",
        {
            "name": "my-tool",
            "version": "0.5.0",
            "description": "My tool",
        },
    )
    (path / "schema.json").write_text(
        json.dumps(
            {
                "name": "my-tool",
                "description": "does thing",
                "parameters": {"type": "object", "properties": {}},
            }
        )
    )
    (path / "executor.py").write_text(
        "class ToolExecutor:\n    async def execute(self, *a, **k):\n        pass\n"
    )


def _make_skill_pkg(path: Path) -> None:
    """Create a single skill package."""
    path.mkdir(parents=True)
    _write_package_json(
        path / "package.json",
        {
            "name": "my-skill",
            "version": "0.5.0",
            "description": "My skill",
        },
    )
    (path / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: A useful skill\n---\n\n# Skill\n",
        encoding="utf-8",
    )


@pytest.fixture
def pm(tmp_path):
    return PackageManager(agent_dir=str(tmp_path / "agent"))


@pytest.fixture(autouse=True)
def no_dependency_install():
    """Prevent tests from running real pip/uv commands."""
    with patch("nova_harness.core.package.core.install_dependencies") as mock:
        yield mock


def test_install_bundle_with_manifest(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)

    meta = pm.install(str(pkg))

    assert meta.name == "test-bundle"
    assert meta.kind == "bundle"
    assert meta.source == str(pkg.resolve())
    assert (pm.agents_dir / "coding_agent" / "description.md").exists()
    assert (pm.tools_dir / "bash" / "schema.json").exists()
    assert len(meta.installed_items) == 2


def test_install_bundle_legacy_no_manifest(pm, tmp_path):
    pkg = tmp_path / "legacy-bundle"
    _make_bundle_pkg(pkg, with_manifest=False)

    meta = pm.install(str(pkg), kind="bundle")

    assert meta.name == "legacy-bundle"
    assert (pm.agents_dir / "coding_agent").exists()
    assert (pm.tools_dir / "bash").exists()


def test_install_agent(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)

    meta = pm.install(str(pkg), kind="agent")

    assert meta.name == "my-agent"
    assert meta.kind == "agent"
    assert (pm.agents_dir / "my-agent" / "description.md").exists()


def test_install_tool(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install(str(pkg), kind="tool")

    assert meta.name == "my-tool"
    assert meta.kind == "tool"
    assert (pm.tools_dir / "my-tool" / "schema.json").exists()


def test_install_with_name_override(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    meta = pm.install(str(pkg), kind="tool", name="renamed-tool")
    assert meta.name == "renamed-tool"
    assert (pm.tools_dir / "renamed-tool").exists()


def test_uninstall_bundle(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install(str(pkg))

    ok = pm.uninstall("test-bundle", kind="bundle")
    assert ok is True
    assert not (pm.agents_dir / "coding_agent").exists()
    assert not (pm.tools_dir / "bash").exists()
    assert pm.info("test-bundle", kind="bundle") is None


def test_update_bundle(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install(str(pkg))

    # Mutate source after install
    (pkg / "agents" / "coding_agent" / "description.md").write_text("updated")

    updated = pm.update("test-bundle", kind="bundle")
    assert updated.name == "test-bundle"
    assert (pm.agents_dir / "coding_agent" / "description.md").read_text() == "updated"


def test_list_includes_unmanaged(pm, tmp_path):
    (pm.agents_dir / "orphan").mkdir(parents=True)
    (pm.agents_dir / "orphan" / "description.md").write_text("orphan")

    pkgs = pm.list()
    orphan = next((p for p in pkgs if p.name == "orphan"), None)
    assert orphan is not None
    assert orphan.kind == "agent"
    assert orphan.version == "unknown"


def test_validate_bundle(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    assert pm.validate(str(pkg)) == []


def test_validate_agent(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    assert pm.validate(str(pkg), kind="agent") == []


def test_validate_tool(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    assert pm.validate(str(pkg), kind="tool") == []


def test_install_skill(pm, tmp_path):
    pkg = tmp_path / "my-skill"
    _make_skill_pkg(pkg)

    meta = pm.install(str(pkg), kind="skill")

    assert meta.name == "my-skill"
    assert meta.kind == "skill"
    assert (pm.skills_dir / "my-skill" / "SKILL.md").exists()


def test_validate_skill(pm, tmp_path):
    pkg = tmp_path / "my-skill"
    _make_skill_pkg(pkg)
    assert pm.validate(str(pkg), kind="skill") == []


def test_validate_bad_bundle(pm, tmp_path):
    pkg = tmp_path / "bad-bundle"
    pkg.mkdir()
    _write_package_json(pkg / "package.json", {"name": "bad"})
    issues = pm.validate(str(pkg), kind="bundle")
    assert any("agents" in i or "tools" in i for i in issues)


def test_no_deps_skips_install(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    with patch("nova_harness.core.package.core.install_dependencies") as mock:
        pm.install(str(pkg), no_deps=True)
        mock.assert_not_called()


def test_dependency_install_called(pm, tmp_path):
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    (pkg / "requirements.txt").write_text("requests>=2.0\n", encoding="utf-8")

    with patch("nova_harness.core.package.core.install_dependencies") as mock:
        pm.install(str(pkg))
        mock.assert_called_once()
        args, kwargs = mock.call_args
        assert "requests>=2.0" in args[0]
        assert kwargs["requirements_path"] == str(pkg / "requirements.txt")


def test_legacy_kind_definition_maps_to_agent(pm, tmp_path):
    pkg = tmp_path / "my-agent"
    _make_agent_pkg(pkg)
    with pytest.warns(DeprecationWarning):
        meta = pm.install(str(pkg), kind="definition")
    assert meta.kind == "agent"


def test_legacy_packages_json_migration(pm, tmp_path):
    # Simulate an old packages.json where a bundle was stored as kind="agent".
    manifest_path = Path(pm.manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "old-bundle",
                        "version": "1.0.0",
                        "description": "",
                        "kind": "agent",
                        "source": "/tmp/old",
                        "install_path": "/tmp/old",
                        "installed_at": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    meta = pm.info("old-bundle", kind="bundle")
    assert meta is not None
    assert meta.kind == "bundle"


def test_project_local_store(tmp_path):
    import os

    cwd = tmp_path / "project"
    cwd.mkdir()
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)

    old_cwd = os.getcwd()
    try:
        os.chdir(str(cwd))
        pm = PackageManager(local=True)
        meta = pm.install(str(pkg), kind="tool")
        assert ".nova" in meta.install_path
    finally:
        os.chdir(old_cwd)


def test_uninstall_unmanaged_single_file(pm, tmp_path):
    """uninstall 应能删除未托管的单个文件包。"""
    skill_file = pm.skills_dir / "orphan.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text("orphan skill", encoding="utf-8")

    ok = pm.uninstall("orphan.md", kind="skill")
    assert ok is True
    assert not skill_file.exists()


def test_uninstall_nothing_when_not_found(pm):
    assert pm.uninstall("missing", kind="tool") is False


def test_list_filters_by_kind(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    pm.install(str(pkg), kind="tool")

    assert len(pm.list(kind="tool")) == 1
    assert pm.list(kind="agent") == []


def test_list_by_bundle_standalone(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    pm.install(str(pkg), kind="tool")

    views = pm.list_by_bundle()
    assert "(standalone)" in views
    assert len(views["(standalone)"].tools) == 1


def test_resolve_kind_invalid(pm, tmp_path):
    pkg = tmp_path / "x"
    pkg.mkdir()
    (pkg / "package.json").write_text('{"name":"x"}')
    with pytest.raises(ValueError):
        pm.install(str(pkg), kind="invalid")


def test_install_bundle_no_entries_raises(pm, tmp_path):
    pkg = tmp_path / "empty-bundle"
    pkg.mkdir()
    (pkg / "package.json").write_text(json.dumps({"name": "empty", "kind": "bundle"}))
    with pytest.raises(ValueError):
        pm.install(str(pkg), kind="bundle")


def test_install_collision_with_unmanaged_directory(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    (pm.tools_dir / "my-tool").mkdir(parents=True)

    with pytest.raises(FileExistsError):
        pm.install(str(pkg), kind="tool")


def test_validate_unknown_kind(pm, tmp_path):
    pkg = tmp_path / "x"
    pkg.mkdir()
    with pytest.raises(ValueError):
        pm.validate(str(pkg))


def test_validate_unresolvable_source(pm):
    issues = pm.validate("/definitely/not/existing/path")
    assert len(issues) == 1


def test_update_not_installed(pm):
    with pytest.raises(ValueError):
        pm.update("missing", kind="tool")


def test_update_without_source(pm, tmp_path):
    pkg = tmp_path / "my-tool"
    _make_tool_pkg(pkg)
    pm.install(str(pkg), kind="tool", name="orphan")
    # 伪造无 source 的 manifest 条目
    data = pm._load_manifest()
    for p in data["packages"]:
        p["source"] = ""
    pm._save_manifest(data)

    with pytest.raises(ValueError):
        pm.update("orphan", kind="tool")


def test_uninstall_bundle_with_missing_items(pm, tmp_path):
    """bundle 卸载时即使某些 installed_items 已不存在也不应报错。"""
    pkg = tmp_path / "test-bundle"
    _make_bundle_pkg(pkg)
    pm.install(str(pkg))

    # 手动删除其中一个已安装条目
    import shutil

    shutil.rmtree(pm.agents_dir / "coding_agent")

    ok = pm.uninstall("test-bundle", kind="bundle")
    assert ok is True


def test_list_includes_unmanaged_tools_and_skills(pm, tmp_path):
    orphan_tool = pm.tools_dir / "orphan-tool"
    orphan_tool.mkdir(parents=True)
    (orphan_tool / "schema.json").write_text(
        json.dumps({"name": "orphan-tool", "parameters": {"type": "object"}}),
        encoding="utf-8",
    )
    (orphan_tool / "executor.py").write_text(
        "class ToolExecutor:\n    async def execute(self, *a, **k):\n        pass\n"
    )

    orphan_skill = pm.skills_dir / "orphan-skill.md"
    orphan_skill.parent.mkdir(parents=True, exist_ok=True)
    orphan_skill.write_text("skill content", encoding="utf-8")

    all_pkgs = pm.list()
    assert any(p.name == "orphan-tool" and p.kind == "tool" for p in all_pkgs)
    assert any(p.name == "orphan-skill.md" and p.kind == "skill" for p in all_pkgs)
