"""Tests for resource/adapters/skills.py and DefaultResourceLoader integration."""

import pytest

from nova_harness.core.resources.loader import DefaultResourceLoader
from nova_harness.core.resources.loaders.skills import load_skills
from nova_harness.core.types.resource import DefaultResourceLoaderOptions


def _write_skill(path, name, description="desc"):
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def test_load_skills_discovers_global_and_project(tmp_path):
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    agent_dir.mkdir()
    cwd.mkdir()

    _write_skill(agent_dir / "skills" / "global-skill", "global-skill")
    _write_skill(cwd / ".nova" / "skills" / "project-skill", "project-skill")

    skills, diagnostics = load_skills(str(cwd), str(agent_dir))
    assert "global-skill" in skills
    assert "project-skill" in skills
    assert not diagnostics


def test_load_skills_collision_uses_priority(tmp_path):
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    agent_dir.mkdir()
    cwd.mkdir()

    _write_skill(agent_dir / "skills" / "shared", "shared", "global desc")
    _write_skill(cwd / ".nova" / "skills" / "shared", "shared", "project desc")

    skills, diagnostics = load_skills(str(cwd), str(agent_dir))
    assert skills["shared"].source_label == "project"
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "collision"


def test_load_skills_no_skips_discovery(tmp_path):
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    agent_dir.mkdir()
    cwd.mkdir()
    _write_skill(agent_dir / "skills" / "x", "x")

    skills, _ = load_skills(str(cwd), str(agent_dir), no_skills=True)
    assert not skills


def test_load_skills_additional_paths(tmp_path):
    agent_dir = tmp_path / "agent"
    extra = tmp_path / "extra"
    agent_dir.mkdir()
    _write_skill(extra, "extra-skill")

    skills, _ = load_skills(
        str(tmp_path / "project"), str(agent_dir), additional_paths=[str(extra)]
    )
    assert "extra-skill" in skills


@pytest.mark.asyncio
async def test_default_resource_loader_get_skills(tmp_path):
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "project"
    agent_dir.mkdir()
    cwd.mkdir()
    _write_skill(agent_dir / "skills" / "loader-skill", "loader-skill")

    loader = DefaultResourceLoader(
        DefaultResourceLoaderOptions(cwd=str(cwd), agent_dir=str(agent_dir))
    )
    await loader.reload()
    skills = loader.get_skills()
    assert "loader-skill" in skills
    assert skills["loader-skill"].description == "desc"
