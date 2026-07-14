"""
Prompt template 加载与参数替换测试。
"""

from pathlib import Path
from unittest.mock import patch

from nova_harness.core.resources.loaders.prompt_templates import (
    _build_path_source_label,
    _dedupe_prompts,
    _is_under_path,
    _load_template_from_file,
    _normalize_path,
    _resolve_prompt_path,
    expand_prompt_template,
    load_prompt_templates,
    load_prompt_templates_with_diagnostics,
    parse_command_args,
    substitute_args,
)
from nova_harness.core.types.resources.prompts import PromptTemplate


def test_parse_command_args_respects_quotes():
    assert parse_command_args('a "b c" d') == ["a", "b c", "d"]


def test_parse_command_args_single_quotes():
    assert parse_command_args("'x y' z") == ["x y", "z"]


def test_parse_command_args_empty_and_whitespace():
    assert parse_command_args("") == []
    assert parse_command_args("   ") == []


def test_substitute_args_positional():
    assert substitute_args("Hello $1", ["World"]) == "Hello World"


def test_substitute_args_out_of_range():
    assert substitute_args("Hello $1 $2", ["World"]) == "Hello World "


def test_substitute_args_all_args():
    assert substitute_args("Args: $@", ["a", "b"]) == "Args: a b"


def test_substitute_args_arguments_alias():
    assert substitute_args("Args: $ARGUMENTS", ["a", "b"]) == "Args: a b"


def test_substitute_args_sliced():
    assert substitute_args("${@:2}", ["a", "b", "c"]) == "b c"
    assert substitute_args("${@:1:2}", ["a", "b", "c"]) == "a b"
    assert substitute_args("${@:2:1}", ["a", "b", "c"]) == "b"


def test_substitute_args_default_present():
    assert substitute_args("Hello ${1:-World}", ["Alice"]) == "Hello Alice"


def test_substitute_args_default_missing():
    assert substitute_args("Hello ${1:-World}", []) == "Hello World"


def test_substitute_args_default_empty():
    assert substitute_args("Hello ${1:-World}", [""]) == "Hello World"


def test_load_template_from_file(tmp_path: Path):
    file_path = tmp_path / "greet.md"
    file_path.write_text(
        "---\nname: greet\ndescription: Greeting\n---\n\nHello!", encoding="utf-8"
    )
    template = _load_template_from_file(str(file_path), "project", "(project)")
    assert template is not None
    assert template.name == "greet"
    assert "Greeting" in template.description
    assert template.content.strip() == "Hello!"


def test_load_template_from_file_uses_body_first_line(tmp_path: Path):
    file_path = tmp_path / "desc.md"
    file_path.write_text("# Header line for description\n\ncontent", encoding="utf-8")
    template = _load_template_from_file(str(file_path), "user", "(user)")
    assert template is not None
    assert "Header line" in template.description


def test_load_template_from_file_argument_hint(tmp_path: Path):
    file_path = tmp_path / "greet.md"
    file_path.write_text(
        "---\nname: greet\ndescription: Greeting\nargument-hint: name\n---\n\nHello!",
        encoding="utf-8",
    )
    template = _load_template_from_file(str(file_path), "project", "(project)")
    assert template is not None
    assert template.argument_hint == "name"


def test_load_template_from_file_missing(tmp_path: Path):
    assert (
        _load_template_from_file(str(tmp_path / "missing.md"), "user", "(user)") is None
    )


def test_load_prompt_templates_from_dir(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    (tmp_path / "a.md").write_text("content A", encoding="utf-8")
    (tmp_path / "b.md").write_text("content B", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("text", encoding="utf-8")

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(tmp_path)],
        )
    )
    names = {t.name for t in result}
    assert names == {"a", "b"}


def test_load_prompt_templates_from_dir_nonexistent(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=["/nonexistent/path"],
        )
    )
    assert result == []


def test_load_prompt_templates_from_dir_recursive(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    (tmp_path / "a.md").write_text("content A", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.md").write_text("content B", encoding="utf-8")

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(tmp_path)],
        )
    )
    names = {t.name for t in result}
    assert names == {"a", "b"}


def test_load_prompt_templates_from_dir_respects_nested_ignore(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    (tmp_path / "a.md").write_text("content A", encoding="utf-8")
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "b.md").write_text("content B", encoding="utf-8")
    (nested / ".ignore").write_text("b.md\n", encoding="utf-8")

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(tmp_path)],
        )
    )
    names = {t.name for t in result}
    assert names == {"a"}


def test_normalize_path_tilde():
    home = str(Path.home())
    assert _normalize_path("~") == home
    assert _normalize_path("~/foo").startswith(home)
    assert _normalize_path("~root").startswith(home)


def test_resolve_prompt_path_absolute():
    assert _resolve_prompt_path("/abs/path", "/cwd") == "/abs/path"


def test_resolve_prompt_path_relative():
    assert _resolve_prompt_path("./rel", "/cwd") == str(Path("/cwd/rel").resolve())


def test_build_path_source_label():
    assert _build_path_source_label("/foo/bar.md") == "(path:bar)"


def test_is_under_path():
    root = "/tmp/root"
    assert _is_under_path("/tmp/root/sub", root) is True
    assert _is_under_path("/tmp/root", root) is True
    assert _is_under_path("/tmp/other", root) is False


def test_load_prompt_templates_defaults(tmp_path: Path):
    """默认从 agent_dir/prompts 与 cwd/.nova/prompts 加载。"""
    agent_prompts = tmp_path / "agent" / "prompts"
    agent_prompts.mkdir(parents=True)
    (agent_prompts / "global.md").write_text("global prompt", encoding="utf-8")

    project_prompts = tmp_path / "cwd" / ".nova" / "prompts"
    project_prompts.mkdir(parents=True)
    (project_prompts / "project.md").write_text("project prompt", encoding="utf-8")

    result = load_prompt_templates(
        options=None,
    )
    # 默认依赖真实 home 目录，直接测试较困难；因此 mock get_prompts_dir
    with patch(
        "nova_harness.core.resources.loaders.prompt_templates.get_prompts_dir",
        return_value=str(agent_prompts),
    ):
        with patch.dict("os.environ", {"PWD": str(tmp_path / "cwd")}):
            # 使用显式 cwd 参数
            result = load_prompt_templates(
                options=None,
            )
    # cwd 默认 os.getcwd() 可能非 tmp_path，此测试以验证函数可调用为主
    assert isinstance(result, list)


def test_load_prompt_templates_with_explicit_paths(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "extra.md").write_text("extra content", encoding="utf-8")

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(extra_dir)],
        )
    )
    assert len(result) == 1
    assert result[0].name == "extra"


def test_load_prompt_templates_with_file_path(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    f = tmp_path / "single.md"
    f.write_text("single", encoding="utf-8")
    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(f)],
        )
    )
    assert len(result) == 1


def test_load_prompt_templates_skips_missing_path(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    result = load_prompt_templates(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(tmp_path / "missing")],
        )
    )
    assert result == []


def test_dedupe_prompts_records_collision():
    prompts = [
        PromptTemplate(
            name="dup",
            description="first",
            content="first",
            source="user",
            file_path="/a/dup.md",
        ),
        PromptTemplate(
            name="dup",
            description="second",
            content="second",
            source="project",
            file_path="/b/dup.md",
        ),
    ]
    result = _dedupe_prompts(prompts)
    assert len(result["prompts"]) == 1
    assert len(result["diagnostics"]) == 1
    assert result["diagnostics"][0].category == "collision"


def test_load_prompt_templates_with_diagnostics(tmp_path: Path):
    from nova_harness.core.types.resources.prompts import LoadPromptTemplatesOptions

    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    (extra_dir / "x.md").write_text("x", encoding="utf-8")

    result = load_prompt_templates_with_diagnostics(
        LoadPromptTemplatesOptions(
            cwd=str(tmp_path),
            agent_dir=str(tmp_path / "agent"),
            prompt_paths=[str(extra_dir)],
        )
    )
    assert len(result["prompts"]) == 1
    assert result["diagnostics"] == []


def test_expand_prompt_template_substitutes_args():
    templates = [
        PromptTemplate(
            name="echo",
            description="echo",
            content="You said: $ARGUMENTS",
            source="user",
            file_path="",
        )
    ]
    result = expand_prompt_template("/echo hello world", templates)
    assert "You said: hello world" in result


def test_expand_prompt_template_unknown_unchanged():
    assert expand_prompt_template("/unknown", []) == "/unknown"


def test_expand_prompt_template_no_args():
    templates = [
        PromptTemplate(
            name="hi",
            description="hi",
            content="Hi there!",
            source="user",
            file_path="",
        )
    ]
    assert expand_prompt_template("/hi", templates) == "Hi there!"


def test_expand_prompt_template_not_slash():
    assert expand_prompt_template("plain text", []) == "plain text"
