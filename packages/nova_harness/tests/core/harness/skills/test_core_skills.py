"""Tests for harness/skills.py runtime management."""

from nova_harness.core.harness.skills import (
    SkillManager,
    expand_skill_command,
    format_skills_for_prompt,
    list_skill_commands,
    parse_skill_block,
)
from nova_harness.core.types.resources.skills import Skill


def _make_skill(tmp_path, name, description="desc", disabled=False):
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n"
        f"disable-model-invocation: {disabled}\n---\n\n# {name}\n\nBody.\n",
        encoding="utf-8",
    )
    return Skill(
        name=name,
        description=description,
        file_path=str(skill_file),
        base_dir=str(skill_dir),
        disable_model_invocation=disabled,
        source_label="test",
    )


def test_format_skills_for_prompt_empty():
    assert format_skills_for_prompt([]) == ""


def test_format_skills_for_prompt_excludes_disabled():
    skills = [
        Skill(
            name="visible",
            description="Visible skill",
            file_path="/tmp/visible/SKILL.md",
            base_dir="/tmp/visible",
            disable_model_invocation=False,
        ),
        Skill(
            name="hidden",
            description="Hidden skill",
            file_path="/tmp/hidden/SKILL.md",
            base_dir="/tmp/hidden",
            disable_model_invocation=True,
        ),
    ]
    prompt = format_skills_for_prompt(skills)
    assert "<name>visible</name>" in prompt
    assert "<name>hidden</name>" not in prompt
    assert "<available_skills>" in prompt
    assert "</available_skills>" in prompt


def test_format_skills_for_prompt_requires_read_tool():
    skill = Skill(
        name="s",
        description="d",
        file_path="/tmp/s/SKILL.md",
        base_dir="/tmp/s",
    )
    assert format_skills_for_prompt([skill], has_read_tool=False) == ""
    assert format_skills_for_prompt([skill], has_read_tool=True) != ""


def test_format_skills_for_prompt_escapes_xml():
    skill = Skill(
        name="x",
        description='Use <read> & "check"',
        file_path='/tmp/x"y/SKILL.md',
        base_dir="/tmp/x",
    )
    prompt = format_skills_for_prompt([skill])
    assert "&lt;read&gt;" in prompt
    assert "&amp;" in prompt
    assert "&quot;check&quot;" in prompt


def test_expand_skill_command(tmp_path):
    skill = _make_skill(tmp_path, "my-skill", "Does a thing")
    skills = {skill.name: skill}

    expanded = expand_skill_command("/skill:my-skill arg1 arg2", skills)
    assert expanded.startswith('<skill name="my-skill"')
    assert "References are relative to" in expanded
    assert "# my-skill" in expanded
    assert "arg1 arg2" in expanded


def test_expand_skill_command_no_args(tmp_path):
    skill = _make_skill(tmp_path, "bare")
    expanded = expand_skill_command("/skill:bare", {"bare": skill})
    assert expanded.endswith("</skill>")
    assert "Body." in expanded


def test_expand_skill_command_unknown():
    assert expand_skill_command("/skill:missing", {}) == "/skill:missing"


def test_expand_skill_command_non_skill_text():
    text = "hello /skill:foo"
    assert expand_skill_command(text, {}) == text


def test_parse_skill_block():
    text = '<skill name="foo" location="/a/SKILL.md">\nContent\n</skill>\n\nUser msg'
    block = parse_skill_block(text)
    assert block is not None
    assert block.name == "foo"
    assert block.location == "/a/SKILL.md"
    assert block.content == "Content"
    assert block.user_message == "User msg"


def test_parse_skill_block_no_user_message():
    text = '<skill name="foo" location="/a/SKILL.md">\nContent\n</skill>'
    block = parse_skill_block(text)
    assert block is not None
    assert block.user_message is None


def test_parse_skill_block_invalid():
    assert parse_skill_block("not a block") is None


def test_list_skill_commands():
    skills = {
        "a": Skill(name="a", description="alpha", file_path="/a", base_dir="/a"),
        "b": Skill(name="b", description="beta", file_path="/b", base_dir="/b"),
    }
    commands = list_skill_commands(skills)
    names = {c.name for c in commands}
    assert names == {"skill:a", "skill:b"}
    assert any(c.description == "alpha" for c in commands)


def test_skill_manager(tmp_path):
    skill = _make_skill(tmp_path, "managed")
    manager = SkillManager({skill.name: skill})

    assert manager.format_for_prompt() != ""
    assert manager.expand_command("/skill:managed do it") != "/skill:managed do it"
    block = manager.parse_block('<skill name="managed" location="x">\nB\n</skill>')
    assert block is not None
    assert block.name == "managed"
    commands = manager.list_commands()
    assert any(c.name == "skill:managed" for c in commands)
