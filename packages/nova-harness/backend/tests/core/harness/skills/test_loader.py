"""Tests for resources/loaders/skills.py file-level loading."""

from nova_harness.core.resources.loaders.skills import (
    load_skill_from_file,
    load_skills,
    load_skills_from_dir,
    validate_description,
    validate_name,
)
from nova_harness.core.types.resources.skills import Skill


def test_validate_name():
    # TS-aligned: only lowercase a-z, 0-9, single hyphens
    assert validate_name("my-skill") == []
    assert validate_name("MySkill") != []
    assert validate_name("my_skill") != []
    assert validate_name("my.skill") != []
    assert validate_name("my--skill") != []
    assert validate_name("") != []
    assert validate_name("-my-skill") != []
    assert validate_name("my-skill-") != []
    assert validate_name("a" * 65) != []


def test_validate_name_messages_align_ts():
    """校验失败文案与 TS validateName 一致，且一次报全所有违规。"""
    assert validate_name("a" * 65) == ["name exceeds 64 characters (65)"]
    assert validate_name("MySkill") == [
        "name contains invalid characters " "(must be lowercase a-z, 0-9, hyphens only)"
    ]
    assert validate_name("-ab") == ["name must not start or end with a hyphen"]
    assert validate_name("a--b") == ["name must not contain consecutive hyphens"]
    # 多重违规一次报全（对齐 TS 的多条诊断粒度）
    assert validate_name("A" * 65) == [
        "name exceeds 64 characters (65)",
        "name contains invalid characters "
        "(must be lowercase a-z, 0-9, hyphens only)",
    ]


def test_validate_description():
    assert validate_description("Does something useful") == []
    assert validate_description("") == ["description is required"]
    assert validate_description("x" * 1025) == [
        "description exceeds 1024 characters (1025)"
    ]


def test_load_skill_from_file(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test\n\nHello.\n",
        encoding="utf-8",
    )
    skill, diagnostics = load_skill_from_file(str(skill_file), source_label="test")
    assert isinstance(skill, Skill)
    assert skill.name == "test-skill"
    assert skill.description == "A test skill"
    assert skill.base_dir == str(tmp_path)
    assert skill.source_label == "test"
    assert diagnostics == []


def test_load_skill_missing_description_rejected_with_warning(tmp_path):
    """description 完全缺失才拒载（附 warning，对齐 TS）。"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("# No frontmatter\n", encoding="utf-8")
    skill, diagnostics = load_skill_from_file(str(skill_file))
    assert skill is None
    messages = [d.message for d in diagnostics]
    assert "description is required" in messages
    assert all(d.category == "warning" for d in diagnostics)
    assert all(d.path == str(skill_file) for d in diagnostics)


def test_load_skill_invalid_name_loads_with_warning(tmp_path):
    """name 非法不拒载——加载并附 warning（对齐 TS 宽松模型）。"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        "---\nname: Bad Name\ndescription: desc\n---\n\nBody\n",
        encoding="utf-8",
    )
    skill, diagnostics = load_skill_from_file(str(skill_file))
    assert isinstance(skill, Skill)
    assert skill.name == "Bad Name"  # 照常加载
    assert any("invalid characters" in d.message for d in diagnostics)


def test_load_skill_overlong_description_loads_with_warning(tmp_path):
    """description 超长不拒载——加载并附 warning（对齐 TS）。"""
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(
        f"---\nname: long-desc\ndescription: {'x' * 1025}\n---\n\nBody\n",
        encoding="utf-8",
    )
    skill, diagnostics = load_skill_from_file(str(skill_file))
    assert isinstance(skill, Skill)
    assert any(
        d.message == "description exceeds 1024 characters (1025)" for d in diagnostics
    )


def test_load_skill_unreadable_file_rejected_with_warning(tmp_path):
    """文件不可读时拒载并附 warning（对齐 TS）。"""
    skill, diagnostics = load_skill_from_file(str("/nonexistent/SKILL.md"))
    assert skill is None
    assert len(diagnostics) == 1
    assert diagnostics[0].category == "warning"


def test_load_skill_from_file_falls_back_to_parent_dir_name(tmp_path):
    skill_dir = tmp_path / "fallback-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\ndescription: A fallback skill\n---\n\nBody\n",
        encoding="utf-8",
    )
    skill, diagnostics = load_skill_from_file(str(skill_file), source_label="test")
    assert isinstance(skill, Skill)
    assert skill.name == "fallback-skill"
    assert skill.description == "A fallback skill"
    assert diagnostics == []


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

    skills, diagnostics = load_skills_from_dir(str(tmp_path))
    names = {s.name for s in skills}
    assert names == {"skill-a", "skill-b", "deep-skill"}
    assert diagnostics == []


def test_load_skills_from_dir_stops_at_skill_md(tmp_path):
    """含 SKILL.md 的目录视为 skill 根，不再递归其子目录（对齐 TS）。"""
    (tmp_path / "parent").mkdir()
    (tmp_path / "parent" / "SKILL.md").write_text(
        "---\nname: parent\ndescription: parent\n---\n", encoding="utf-8"
    )
    (tmp_path / "parent" / "child").mkdir(parents=True)
    (tmp_path / "parent" / "child" / "SKILL.md").write_text(
        "---\nname: child\ndescription: child\n---\n", encoding="utf-8"
    )

    skills, _ = load_skills_from_dir(str(tmp_path))
    assert len(skills) == 1
    assert skills[0].name == "parent"


def test_load_skills_from_dir_loads_root_level_markdown(tmp_path):
    """第一层目录的散装 .md 文件作为 skill 加载；递归层不加载（对齐 TS）。"""
    # 第一层散装 .md → 加载
    (tmp_path / "loose.md").write_text(
        "---\nname: loose\ndescription: loose skill\n---\n\nL\n", encoding="utf-8"
    )
    # 递归层的散装 .md → 不加载
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested-loose.md").write_text(
        "---\nname: nested-loose\ndescription: nested\n---\n\nN\n", encoding="utf-8"
    )

    skills, _ = load_skills_from_dir(str(tmp_path))
    assert {s.name for s in skills} == {"loose"}


def test_load_skills_from_dir_skips_hidden_and_node_modules(tmp_path):
    """跳过 . 开头条目与各生态依赖/缓存目录（对齐 TS 并 Python 化）。"""
    (tmp_path / "node_modules" / "dep").mkdir(parents=True)
    (tmp_path / "node_modules" / "dep" / "SKILL.md").write_text(
        "---\nname: dep-skill\ndescription: dep\n---\n", encoding="utf-8"
    )
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "SKILL.md").write_text(
        "---\nname: venv-skill\ndescription: venv\n---\n", encoding="utf-8"
    )
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "SKILL.md").write_text(
        "---\nname: pyc-skill\ndescription: pyc\n---\n", encoding="utf-8"
    )
    (tmp_path / ".hidden" / "secret").mkdir(parents=True)
    (tmp_path / ".hidden" / "secret" / "SKILL.md").write_text(
        "---\nname: secret-skill\ndescription: secret\n---\n", encoding="utf-8"
    )
    (tmp_path / ".hidden-root.md").write_text(
        "---\nname: hidden-file\ndescription: hidden\n---\n", encoding="utf-8"
    )
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "SKILL.md").write_text(
        "---\nname: real-skill\ndescription: real\n---\n", encoding="utf-8"
    )

    skills, _ = load_skills_from_dir(str(tmp_path))
    assert {s.name for s in skills} == {"real-skill"}


def test_load_skills_missing_explicit_path_warns(tmp_path):
    """显式路径不存在产生 warning（对齐 TS），但不影响其他路径加载。"""
    (tmp_path / "skill-a").mkdir()
    (tmp_path / "skill-a" / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: first\n---\n\nA\n", encoding="utf-8"
    )
    missing = str(tmp_path / "does-not-exist")

    skills, diagnostics = load_skills(additional_paths=[missing, str(tmp_path)])
    assert set(skills.keys()) == {"skill-a"}
    assert any(
        d.category == "warning"
        and d.message == "skill path does not exist"
        and d.path == missing
        for d in diagnostics
    )


def test_load_skills_non_markdown_explicit_file_warns(tmp_path):
    """显式路径既不是目录也不是 .md 文件时产生 warning（对齐 TS）。"""
    not_md = tmp_path / "notes.txt"
    not_md.write_text("hello", encoding="utf-8")

    skills, diagnostics = load_skills(additional_paths=[str(not_md)])
    assert skills == {}
    assert any(
        d.category == "warning" and d.message == "skill path is not a markdown file"
        for d in diagnostics
    )
