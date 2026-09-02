"""测试资源自动发现。"""

import os

import pytest
from nova_harness.core.package.resolve.discovery import (
    collect_auto_prompt_entries,
    collect_extension_entries,
    collect_prompt_entries,
    collect_skill_entries,
    collect_tool_entries,
)


def test_collect_extension_entries_file(tmp_path) -> None:
    ext_file = tmp_path / "extension.py"
    ext_file.write_text("def extension(nova): pass")
    assert collect_extension_entries(str(tmp_path)) == [str(ext_file.resolve())]


def test_collect_extension_entries_dir(tmp_path) -> None:
    ext_dir = tmp_path / "my_ext"
    ext_dir.mkdir()
    (ext_dir / "extension.py").write_text("")
    assert collect_extension_entries(str(tmp_path)) == [str(ext_dir.resolve())]


def test_collect_extension_entries_from_pyproject_manifest(tmp_path) -> None:
    """目录自身声明 [tool.nova.extensions] 时应按 manifest 展开入口。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def extension(nova): pass")

    (tmp_path / "pyproject.toml").write_text(
        '[tool.nova]\nextensions = ["src/main.py"]\n',
        encoding="utf-8",
    )

    entries = collect_extension_entries(str(tmp_path))
    assert entries == [str((src / "main.py").resolve())]


def test_collect_extension_entries_pyproject_empty_list_disables_discovery(
    tmp_path,
) -> None:
    """[tool.nova.extensions] 显式为空列表时应禁用该目录的扩展发现。"""
    ext_dir = tmp_path / "my_ext"
    ext_dir.mkdir()
    (ext_dir / "extension.py").write_text("")

    (tmp_path / "pyproject.toml").write_text(
        "[tool.nova]\nextensions = []\n",
        encoding="utf-8",
    )

    assert collect_extension_entries(str(tmp_path)) == []


def test_collect_extension_entries_from_pyproject_manifest_multiple(tmp_path) -> None:
    """manifest 声明多个入口时应全部返回。"""
    a = tmp_path / "a.py"
    a.write_text("def extension(nova): pass")
    b = tmp_path / "b.py"
    b.write_text("def extension(nova): pass")

    (tmp_path / "pyproject.toml").write_text(
        '[tool.nova]\nextensions = ["a.py", "b.py"]\n',
        encoding="utf-8",
    )

    entries = collect_extension_entries(str(tmp_path))
    assert str(a.resolve()) in entries
    assert str(b.resolve()) in entries
    assert len(entries) == 2


def test_collect_skill_entries(tmp_path) -> None:
    skill_dir = tmp_path / "python"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: python\n---")
    assert collect_skill_entries(str(tmp_path)) == [str(skill_dir.resolve())]


def test_collect_skill_entries_respects_gitignore(tmp_path) -> None:
    """skill 发现应尊重 .gitignore 中的排除规则。"""
    ignored_dir = tmp_path / "draft"
    ignored_dir.mkdir()
    (ignored_dir / "SKILL.md").write_text("---\nname: draft\n---")

    kept_dir = tmp_path / "python"
    kept_dir.mkdir()
    (kept_dir / "SKILL.md").write_text("---\nname: python\n---")

    (tmp_path / ".gitignore").write_text("draft/\n", encoding="utf-8")

    assert collect_skill_entries(str(tmp_path)) == [str(kept_dir.resolve())]


def test_collect_skill_entries_respects_nested_gitignore(tmp_path) -> None:
    """子目录下的 .gitignore 也应生效。"""
    nested = tmp_path / "nested"
    nested.mkdir()

    keep = nested / "keep"
    keep.mkdir()
    (keep / "SKILL.md").write_text("---\nname: keep\n---")

    ignored = nested / "draft"
    ignored.mkdir()
    (ignored / "SKILL.md").write_text("---\nname: draft\n---")

    (nested / ".gitignore").write_text("draft/\n", encoding="utf-8")

    skills = collect_skill_entries(str(tmp_path))
    assert skills == [str(keep.resolve())]


def test_collect_skill_entries_root_gitignore_filters_subdir(tmp_path) -> None:
    """资源根目录的 .gitignore 可排除整个子目录。"""
    (tmp_path / ".gitignore").write_text("draft/\n", encoding="utf-8")

    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "SKILL.md").write_text("---\nname: draft\n---")

    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "SKILL.md").write_text("---\nname: keep\n---")

    skills = collect_skill_entries(str(tmp_path))
    assert skills == [str(keep.resolve())]


def test_collect_prompt_entries(tmp_path) -> None:
    prompt = tmp_path / "test.md"
    prompt.write_text("hello")
    assert collect_prompt_entries(str(tmp_path)) == [str(prompt.resolve())]


def test_collect_prompt_entries_respects_gitignore(tmp_path) -> None:
    """prompt 发现应尊重 .gitignore 中的排除规则。"""
    (tmp_path / "debug.md").write_text("debug", encoding="utf-8")
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("draft.md\n", encoding="utf-8")

    assert collect_prompt_entries(str(tmp_path)) == [
        str((tmp_path / "debug.md").resolve())
    ]


def test_collect_prompt_entries_respects_nested_gitignore(tmp_path) -> None:
    """子目录下的 .gitignore 对 prompt 也应生效。"""
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "keep.md").write_text("keep", encoding="utf-8")
    (nested / "draft.md").write_text("draft", encoding="utf-8")
    (nested / ".gitignore").write_text("draft.md\n", encoding="utf-8")

    prompts = collect_prompt_entries(str(tmp_path))
    assert prompts == [str((nested / "keep.md").resolve())]


def test_collect_prompt_entries_root_gitignore_filters_subdir(tmp_path) -> None:
    """资源根目录的 .gitignore 可排除整个 prompt 子目录。"""
    (tmp_path / ".gitignore").write_text("draft/\n", encoding="utf-8")

    draft = tmp_path / "draft"
    draft.mkdir()
    (draft / "debug.md").write_text("debug", encoding="utf-8")

    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "debug.md").write_text("debug", encoding="utf-8")

    prompts = collect_prompt_entries(str(tmp_path))
    assert prompts == [str((keep / "debug.md").resolve())]


def test_collect_extension_entries_respects_gitignore(tmp_path) -> None:
    """extension 发现应尊重 .gitignore 中的排除规则。"""
    ext_file = tmp_path / "extension.py"
    ext_file.write_text("def extension(nova): pass")
    ignored = tmp_path / "ignored.py"
    ignored.write_text("def extension(nova): pass")
    (tmp_path / ".gitignore").write_text("ignored.py\n", encoding="utf-8")

    assert collect_extension_entries(str(tmp_path)) == [str(ext_file.resolve())]


def test_collect_tool_entries_respects_gitignore(tmp_path) -> None:
    """tool 发现应尊重 .gitignore 中的排除规则。"""
    tool_dir = tmp_path / "bash"
    tool_dir.mkdir()
    (tool_dir / "executor.py").write_text("# executor")

    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "executor.py").write_text("# executor")

    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")

    assert collect_tool_entries(str(tmp_path)) == [str(tool_dir.resolve())]


def test_collect_agent_entries_respects_gitignore(tmp_path) -> None:
    """agent 发现应尊重 .gitignore 中的排除规则（顶层 *.yaml，扁平不递归）。"""
    from nova_harness.core.package.resolve.discovery import collect_agent_entries

    agent_yaml = tmp_path / "coding.yaml"
    agent_yaml.write_text("description: coding agent\n")
    (tmp_path / "ignored.yaml").write_text("description: ignored agent\n")
    # 子目录中的 yaml 不属于扁平组合层，不应被收集
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "nested.yaml").write_text("description: nested\n")

    (tmp_path / ".gitignore").write_text("ignored.yaml\n", encoding="utf-8")

    assert collect_agent_entries(str(tmp_path)) == [str(agent_yaml.resolve())]


def test_collect_explicit_respects_gitignore_for_files(tmp_path):
    """manifest 显式声明的文件路径应被 .gitignore 过滤。"""
    from nova_harness.core.package.resolve.discovery import collect_explicit

    (tmp_path / "keep.md").write_text("keep")
    (tmp_path / "ignored.md").write_text("ignored")
    (tmp_path / ".gitignore").write_text("ignored.md\n", encoding="utf-8")

    result = collect_explicit(["./keep.md", "./ignored.md"], tmp_path)

    assert str((tmp_path / "keep.md").resolve()) in result
    assert str((tmp_path / "ignored.md").resolve()) not in result


def test_collect_explicit_respects_gitignore_for_dirs(tmp_path):
    """manifest 显式声明的目录路径应递归扫描，并应用 .gitignore 过滤。"""
    from nova_harness.core.package.resolve.discovery import (
        ResourceType,
        collect_explicit,
    )

    keep = tmp_path / "keep"
    keep.mkdir()
    (keep / "keep.md").write_text("keep", encoding="utf-8")

    ignored = tmp_path / "ignored"
    ignored.mkdir()
    (ignored / "ignored.md").write_text("ignored", encoding="utf-8")

    (tmp_path / ".gitignore").write_text("ignored/\n", encoding="utf-8")

    result = collect_explicit(
        ["./keep", "./ignored"], tmp_path, resource_type=ResourceType.PROMPTS
    )

    assert str((keep / "keep.md").resolve()) in result
    assert str((ignored / "ignored.md").resolve()) not in result
    assert str(keep.resolve()) not in result
    assert str(ignored.resolve()) not in result


def test_collect_explicit_glob_respects_gitignore(tmp_path):
    """manifest 中的 glob 模式结果应被 .gitignore 过滤。"""
    from nova_harness.core.package.resolve.discovery import collect_explicit

    (tmp_path / "keep.md").write_text("keep")
    (tmp_path / "ignored.md").write_text("ignored")
    (tmp_path / ".gitignore").write_text("ignored.md\n", encoding="utf-8")

    result = collect_explicit(["./*.md"], tmp_path)

    assert str((tmp_path / "keep.md").resolve()) in result
    assert str((tmp_path / "ignored.md").resolve()) not in result


def test_collect_explicit_with_override_patterns(tmp_path):
    """manifest 中的 !/+/- override 模式应生效。"""
    from nova_harness.core.package.resolve.discovery import collect_explicit

    (tmp_path / "keep.md").write_text("keep")
    (tmp_path / "exclude.md").write_text("exclude")
    (tmp_path / "force.md").write_text("force")

    items = ["./*.md", "!./exclude.md", "+./force.md"]
    result = collect_explicit(items, tmp_path)

    assert str((tmp_path / "keep.md").resolve()) in result
    assert str((tmp_path / "exclude.md").resolve()) not in result
    assert str((tmp_path / "force.md").resolve()) in result


def test_collect_explicit_directory_as_skill(tmp_path):
    """manifest 显式声明的目录本身即为 skill 时应返回该目录。"""
    from nova_harness.core.package.resolve.discovery import (
        ResourceType,
        collect_explicit,
    )

    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---", encoding="utf-8")

    result = collect_explicit(
        ["./my-skill"], tmp_path, resource_type=ResourceType.SKILLS
    )
    assert str(skill_dir.resolve()) in result


def test_collect_explicit_directory_recursive_for_prompts(tmp_path):
    """manifest 显式声明的 prompt 目录应递归发现其内部 .md 文件。"""
    from nova_harness.core.package.resolve.discovery import (
        ResourceType,
        collect_explicit,
    )

    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    nested = prompt_dir / "nested"
    nested.mkdir()
    (nested / "hello.md").write_text("hello", encoding="utf-8")

    result = collect_explicit(
        ["./prompts"], tmp_path, resource_type=ResourceType.PROMPTS
    )
    assert str((nested / "hello.md").resolve()) in result
    assert str(prompt_dir.resolve()) not in result


def test_collect_explicit_directory_recursive_for_extensions(tmp_path):
    """manifest 显式声明的 extension 目录应递归发现内部扩展。"""
    from nova_harness.core.package.resolve.discovery import (
        ResourceType,
        collect_explicit,
    )

    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    sub = ext_dir / "sub"
    sub.mkdir()
    (sub / "extension.py").write_text("def extension(nova): pass", encoding="utf-8")

    result = collect_explicit(
        ["./extensions"], tmp_path, resource_type=ResourceType.EXTENSIONS
    )
    assert str(sub.resolve()) in result
    assert str(ext_dir.resolve()) not in result
    """manifest 中的 glob 匹配到目录时也应递归扫描。"""
    from nova_harness.core.package.resolve.discovery import (
        ResourceType,
        collect_explicit,
    )

    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "debug.md").write_text("# debug", encoding="utf-8")

    result = collect_explicit(["./*"], tmp_path, resource_type=ResourceType.PROMPTS)
    assert str((prompt_dir / "debug.md").resolve()) in result


def test_collect_explicit_glob_escapes_package_root(tmp_path):
    """glob 模式中出现 .. 应视为逃逸包根并拒绝。"""
    from nova_harness.core.package.resolve.discovery import collect_explicit

    (tmp_path / "keep.md").write_text("keep")
    parent = tmp_path.parent
    (parent / "outside.md").write_text("outside")

    with pytest.raises(ValueError, match="escapes package root"):
        collect_explicit(["../*.md"], tmp_path)


def test_collect_ancestor_agents_skills_dirs(tmp_path):
    """应从 start_dir 向上收集 .agents/skills 目录直到 git root。"""
    from nova_harness.core.package.resolve.discovery import (
        collect_ancestor_agents_skills_dirs,
    )

    # 创建 git repo
    (tmp_path / ".git").mkdir()

    child = tmp_path / "a" / "b" / "c"
    child.mkdir(parents=True)

    ancestor_skill = tmp_path / "a" / ".agents" / "skills"
    ancestor_skill.mkdir(parents=True)

    # git root 之外的 skill 不应被收集
    outside = tmp_path.parent / ".agents" / "skills"
    outside.mkdir(parents=True)

    try:
        dirs = collect_ancestor_agents_skills_dirs(str(child), stop_at_git_root=True)
        assert dirs == [str(ancestor_skill.resolve())]
    finally:
        # 清理共享 pytest 根目录下的测试目录，避免污染其他测试。
        import shutil

        shutil.rmtree(outside, ignore_errors=True)


def test_nested_ignore_negation_reincludes(tmp_path) -> None:
    """子级 .gitignore 的 ``!`` 反选可以恢复被上层忽略的路径（last-match-wins）。"""
    from nova_harness.core.package.utils import (
        is_ignored_by_specs,
        load_ignore_specs,
    )

    (tmp_path / ".gitignore").write_text("*.md\n", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / ".gitignore").write_text("!keep.md\n", encoding="utf-8")

    specs = load_ignore_specs(str(tmp_path))

    assert is_ignored_by_specs("docs/other.md", is_dir=False, specs=specs) is True
    assert is_ignored_by_specs("docs/keep.md", is_dir=False, specs=specs) is False
    assert is_ignored_by_specs("root.md", is_dir=False, specs=specs) is True
    assert is_ignored_by_specs("main.py", is_dir=False, specs=specs) is False


def test_collect_extension_entries_does_not_recurse_non_extension_dirs(
    tmp_path,
) -> None:
    """非扩展目录不再递归——辅助模块不会被当扩展加载执行（对齐 TS）。"""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "helpers.py").write_text("def extension(nova): pass")
    deeper = lib / "deeper"
    deeper.mkdir()
    (deeper / "x.py").write_text("def extension(nova): pass")

    assert collect_extension_entries(str(tmp_path)) == []


def test_collect_extension_entries_root_files_and_extension_dirs_only(tmp_path) -> None:
    """根级 .py 与合法扩展子目录照常收集；扩展目录内部的 .py 不单独收集。"""
    root_file = tmp_path / "top.py"
    root_file.write_text("def extension(nova): pass")
    ext_dir = tmp_path / "my_ext"
    ext_dir.mkdir()
    (ext_dir / "extension.py").write_text("")
    (ext_dir / "utils.py").write_text("")

    entries = collect_extension_entries(str(tmp_path))
    assert str(root_file.resolve()) in entries
    assert str(ext_dir.resolve()) in entries
    assert len(entries) == 2


def test_collect_extension_entries_subdir_manifest(tmp_path) -> None:
    """直接子目录自身声明 [tool.nova.extensions] 时按其 manifest 展开。"""
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "pyproject.toml").write_text(
        '[tool.nova]\nextensions = ["main.py"]\n',
        encoding="utf-8",
    )
    (sub / "main.py").write_text("def extension(nova): pass")

    entries = collect_extension_entries(str(tmp_path))
    assert entries == [str((sub / "main.py").resolve())]


def test_collect_auto_prompt_entries_flat_only(tmp_path) -> None:
    """顶层自动发现的 prompts 只收当前层级 .md（对齐 TS collectAutoPromptEntries）。"""
    (tmp_path / "a.md").write_text("a")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.md").write_text("b")

    entries = collect_auto_prompt_entries(str(tmp_path))
    assert entries == [str((tmp_path / "a.md").resolve())]


def test_collect_prompt_entries_recursive_for_packages(tmp_path) -> None:
    """包内 prompts 保持递归（对齐 TS collectResourceFiles）。"""
    (tmp_path / "a.md").write_text("a")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.md").write_text("b")

    entries = collect_prompt_entries(str(tmp_path))
    assert str((tmp_path / "a.md").resolve()) in entries
    assert str((nested / "b.md").resolve()) in entries
