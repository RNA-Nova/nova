"""Tests for resources/loaders/skills.py file-level loading."""

from nova_harness.core.resources.loaders.skills import (
    load_skill_from_file,
    load_skills_from_dir,
    validate_description,
    validate_name,
)
from nova_harness.core.types.skills import Skill


def test_validate_name():
    assert validate_name("my-skill")[0] is True
    # TS-aligned: only lowercase a-z, 0-9, single hyphens
    assert validate_name("MySkill")[0] is False
    assert validate_name("my_skill")[0] is False
    assert validate_name("my.skill")[0] is False
    assert validate_name("my--skill")[0] is False
    assert validate_name("")[0] is False
    assert validate_name("-my-skill")[0] is False
    assert validate_name("my-skill-")[0] is False
    assert validate_name("a" * 65)[0] is False


def test_validate_description():
    assert validate_description("Does something useful")[0] is True
    assert validate_description("")[0] is False
    assert validate_description("x" * 1025)[0] is False


def test_load_skill_from_file(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test\n\nHello.\n",
        encoding="utf-8",
    )
    skill = load_skill_from_file(str(skill_file), source_label="test")
    assert isinstance(skill, Skill)
    assert skill.name == "test-skill"
    assert skill.description == "A test skill"
    assert skill.base_dir == str(tmp_path)
    assert skill.source_label == "test"


def test_load_skill_missing_frontmatter(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# No frontmatter\n", encoding="utf-8")
    assert load_skill_from_file(str(skill_file)) is None


def test_load_skill_invalid_name(tmp_path):
    # 父目录名也无效（含空格），确保 fallback 后仍返回 None
    bad_dir = tmp_path / "bad dir"
    bad_dir.mkdir()
    skill_file = bad_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: Bad Name\ndescription: desc\n---\n\nBody\n",
        encoding="utf-8",
    )
    assert load_skill_from_file(str(skill_file)) is None


def test_load_skill_from_file_falls_back_to_parent_dir_name(tmp_path):
    skill_dir = tmp_path / "fallback-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\ndescription: A fallback skill\n---\n\nBody\n",
        encoding="utf-8",
    )
    skill = load_skill_from_file(str(skill_file), source_label="test")
    assert isinstance(skill, Skill)
    assert skill.name == "fallback-skill"
    assert skill.description == "A fallback skill"


def test_load_skills_from_dir(tmp_path):
    (tmp_path / "skill-a").mkdir()
    (tmp_path / "skill-a" / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: first\n---\n\nA\n", encoding="utf-8"
    )
    (tmp_path / "skill-b").mkdir()
    (tmp_path / "skill-b" / "SKILL.md").write_text(
        "---\nname: skill-b\ndescription: second\n---\n\nB\n", encoding="utf-8"
    )
    # A nested skill under a directory that itself has no SKILL.md
    (tmp_path / "nested" / "deep").mkdir(parents=True)
    (tmp_path / "nested" / "deep" / "SKILL.md").write_text(
        "---\nname: deep-skill\ndescription: deep\n---\n\nD\n", encoding="utf-8"
    )

    skills = load_skills_from_dir(str(tmp_path))
    names = {s.name for s in skills}
    assert names == {"skill-a", "skill-b", "deep-skill"}


def test_load_skills_from_dir_stops_at_skill_md(tmp_path):
    (tmp_path / "parent").mkdir()
    (tmp_path / "parent" / "SKILL.md").write_text(
        "---\nname: parent\ndescription: parent\n---\n", encoding="utf-8"
    )
    (tmp_path / "parent" / "child").mkdir(parents=True)
    (tmp_path / "parent" / "child" / "SKILL.md").write_text(
        "---\nname: child\ndescription: child\n---\n", encoding="utf-8"
    )

    skills = load_skills_from_dir(str(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "parent"


def test_load_skills_from_dir_allowed_names_filters(tmp_path):
    (tmp_path / "skill-a").mkdir()
    (tmp_path / "skill-a" / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: first\n---\n\nA\n", encoding="utf-8"
    )
    (tmp_path / "skill-b").mkdir()
    (tmp_path / "skill-b" / "SKILL.md").write_text(
        "---\nname: skill-b\ndescription: second\n---\n\nB\n", encoding="utf-8"
    )

    skills = load_skills_from_dir(str(tmp_path), allowed_names={"skill-a"})
    names = {s.name for s in skills}
    assert names == {"skill-a"}


def test_load_skills_allowed_names_filters(tmp_path):
    from nova_harness.core.resources.loaders.skills import load_skills

    (tmp_path / "skill-a").mkdir()
    (tmp_path / "skill-a" / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: first\n---\n\nA\n", encoding="utf-8"
    )
    (tmp_path / "skill-b").mkdir()
    (tmp_path / "skill-b" / "SKILL.md").write_text(
        "---\nname: skill-b\ndescription: second\n---\n\nB\n", encoding="utf-8"
    )

    skills, diagnostics = load_skills(
        additional_paths=[str(tmp_path)], allowed_names={"skill-b"}
    )
    assert set(skills.keys()) == {"skill-b"}
    assert diagnostics == []
