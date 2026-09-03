"""Tests for skills loader and DefaultResourceLoader integration."""

from pathlib import Path

import pytest
from nova_harness.core.package import PackageManager
from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.resources.loaders.skills import load_skills
from nova_harness.core.types.package import (
    PathMetadata,
    ResolvedResource,
    SourceOrigin,
    SourceScope,
)
from nova_harness.core.types.resources.loader import DefaultResourceLoaderOptions
from tests._helpers.settings_manager import settings_manager_in_memory


def _write_skill(path, name, description="desc"):
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _skill_resource(
    path: Path, scope: SourceScope = SourceScope.PROJECT
) -> ResolvedResource:
    return ResolvedResource(
        path=str(path),
        enabled=True,
        metadata=PathMetadata(
            source="auto",
            scope=scope,
            origin=SourceOrigin.TOP_LEVEL,
        ),
    )


def test_load_skills_from_resolved_resources(tmp_path):
    skill_dir = tmp_path / "skills" / "my-skill"
    _write_skill(skill_dir, "my-skill")

    skills, diagnostics = load_skills(resolved_resources=[_skill_resource(skill_dir)])
    assert "my-skill" in skills
    assert not diagnostics


def test_load_skills_collision_uses_priority(tmp_path):
    global_skill = tmp_path / "global" / "shared"
    project_skill = tmp_path / "project" / "shared"
    _write_skill(global_skill, "shared", "global desc")
    _write_skill(project_skill, "shared", "project desc")

    # resolver 会按优先级排序，project 排在 user 之前，因此先加载的 project 胜出
    skills, diagnostics = load_skills(
        resolved_resources=[
            _skill_resource(project_skill, SourceScope.PROJECT),
            _skill_resource(global_skill, SourceScope.USER),
        ]
    )
    assert skills["shared"].source_label == "project"
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "collision"


def test_load_skills_source_info_path_points_to_skill_file(tmp_path):
    """skill 的 source_info.path 指向实际 SKILL.md 文件，而非共享的资源根路径。"""
    skill_dir = tmp_path / "skills" / "my-skill"
    _write_skill(skill_dir, "my-skill")

    skills, _ = load_skills(resolved_resources=[_skill_resource(skill_dir)])
    skill = skills["my-skill"]
    assert skill.source_info is not None
    assert skill.source_info.path == str((skill_dir / "SKILL.md").resolve())


def test_load_skills_no_skips(tmp_path):
    skill_dir = tmp_path / "skills" / "x"
    _write_skill(skill_dir, "x")

    skills, _ = load_skills(
        resolved_resources=[_skill_resource(skill_dir)], no_skills=True
    )
    assert not skills


def test_load_skills_additional_paths(tmp_path):
    extra = tmp_path / "extra"
    _write_skill(extra, "extra-skill")

    skills, _ = load_skills(additional_paths=[str(extra)])
    assert "extra-skill" in skills


@pytest.mark.asyncio
async def test_default_resource_loader_get_skills(tmp_path):
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    agent_dir.mkdir()
    cwd.mkdir()
    _write_skill(agent_dir / "backend" / "skills" / "loader-skill", "loader-skill")

    settings_manager = settings_manager_in_memory(project_trusted=True)
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
            package_manager=package_manager,
        )
    )
    await loader.reload()
    skills = loader.get_skills()
    assert "loader-skill" in skills["skills"]
    assert skills["skills"]["loader-skill"].description == "desc"
