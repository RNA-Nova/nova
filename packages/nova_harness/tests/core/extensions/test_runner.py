"""ExtensionRunner 核心行为测试。"""

from types import SimpleNamespace

import pytest

from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events import InputEvent, InputEventResult
from nova_harness.core.types.events.constants import INPUT
from nova_harness.core.types.extensions import (
    Extension,
    ExtensionRuntime,
    RegisteredCommand,
)
from nova_harness.core.types.project_trust import (
    ProjectTrustContext,
    ProjectTrustEvent,
    ProjectTrustEventResult,
)
from nova_harness.core.ui.noop import NoOpUIContext


def _minimal_runtime() -> ExtensionRuntime:
    """构造一个足够运行 create_context 的最小 runtime。"""
    runtime = ExtensionRuntime(cwd="/tmp")

    core_actions = [
        "send_message",
        "send_user_message",
        "exec",
        "append_entry",
        "set_session_name",
        "get_session_name",
        "set_label",
        "get_active_tools",
        "get_all_tools",
        "set_active_tools",
        "refresh_tools",
        "get_commands",
        "set_model",
        "get_thinking_level",
        "set_thinking_level",
    ]
    for name in core_actions:
        setattr(runtime, name, lambda *args, **kwargs: None)

    runtime.context_actions = SimpleNamespace(
        get_model=lambda: None,
        is_idle=lambda: True,
        is_project_trusted=lambda: True,
        get_signal=lambda: None,
        abort=lambda: None,
        has_pending_messages=lambda: False,
        shutdown=lambda: None,
        get_context_usage=lambda: None,
        compact=lambda: None,
        get_system_prompt=lambda: "",
        get_system_prompt_options=lambda: {},
    )
    return runtime


def _runner(extensions):
    return ExtensionRunner(
        extensions=extensions,
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_registry=None,
    )


# -----------------------------------------------------------------------------
# 命令注册与自动重命名
# -----------------------------------------------------------------------------


def test_get_registered_commands_unique_unchanged():
    runner = _runner(
        [Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})]
    )
    cmds = runner.get_registered_commands()
    assert [c.resolved_name() for c in cmds] == ["foo"]
    assert [c.name for c in cmds] == ["foo"]


def test_get_registered_commands_duplicates_renamed():
    """同名命令按 TS resolveRegisteredCommands 行为生成 name:1、name:2 ..."""
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    ext3 = Extension(path="e3", commands={"foo": RegisteredCommand(name="foo")})
    runner = _runner([ext1, ext2, ext3])
    cmds = runner.get_registered_commands()
    assert [c.resolved_name() for c in cmds] == ["foo:1", "foo:2", "foo:3"]
    # 原始名保留
    assert [c.name for c in cmds] == ["foo", "foo", "foo"]


def test_get_registered_commands_collision_with_existing_name():
    """自动生成的调用名与后续原始命令名冲突时递增避让。"""
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    ext3 = Extension(path="e3", commands={"foo:2": RegisteredCommand(name="foo:2")})
    runner = _runner([ext1, ext2, ext3])
    cmds = runner.get_registered_commands()
    assert [c.resolved_name() for c in cmds] == ["foo:1", "foo:2", "foo:2:2"]
    assert [c.name for c in cmds] == ["foo", "foo", "foo:2"]


def test_get_command_finds_by_invocation_name():
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    runner = _runner([ext1, ext2])
    assert runner.get_command("foo:1") is not None
    assert runner.get_command("foo:2") is not None
    assert runner.get_command("foo") is None


def test_get_command_by_original_name_ignores_rename():
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    runner = _runner([ext1, ext2])
    # 原始名查找返回扩展中注册的命令对象本身
    assert runner.get_command_by_original_name("foo").name == "foo"


# -----------------------------------------------------------------------------
# emit_input
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_input_no_handlers_returns_continue():
    runner = _runner([])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "continue"
    assert result.text is None
    assert result.images is None


@pytest.mark.asyncio
async def test_emit_input_transform_chains():
    def handler(event, ctx):
        return InputEventResult(action="transform", text=event.text.upper())

    ext = Extension(path="e1", handlers={INPUT: [handler]})
    runner = _runner([ext])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "transform"
    assert result.text == "HELLO"


@pytest.mark.asyncio
async def test_emit_input_transform_updates_images():
    img = {"type": "image", "url": "http://example.com/a.png"}

    def handler(event, ctx):
        return InputEventResult(action="transform", images=[img])

    ext = Extension(path="e1", handlers={INPUT: [handler]})
    runner = _runner([ext])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "transform"
    assert result.images == [img]


@pytest.mark.asyncio
async def test_emit_input_handled_short_circuits():
    calls = []

    def first(event, ctx):
        calls.append("first")
        return InputEventResult(action="handled")

    def second(event, ctx):
        calls.append("second")
        return InputEventResult(action="transform", text="never")

    ext = Extension(path="e1", handlers={INPUT: [first, second]})
    runner = _runner([ext])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "handled"
    assert calls == ["first"]


@pytest.mark.asyncio
async def test_emit_input_continue_does_not_change_text():
    def handler(event, ctx):
        return InputEventResult(action="continue")

    ext = Extension(path="e1", handlers={INPUT: [handler]})
    runner = _runner([ext])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "continue"


@pytest.mark.asyncio
async def test_api_on_input_subscribes_input_event():
    """通过 api.on_input 订阅 input 事件，验证 API 层行为一致。"""
    from nova_harness.core.extensions.api import create_extension_api

    ext = Extension(path="e1")
    runtime = _minimal_runtime()
    api = create_extension_api(ext, runtime)

    def handler(event, ctx):
        return InputEventResult(action="transform", text=event.text + "!")

    api.on_input(handler)

    runner = ExtensionRunner(
        extensions=[ext],
        runtime=runtime,
        cwd="/tmp",
        session_manager=None,
        model_registry=None,
    )
    result = await runner.emit_input(InputEvent(text="hi"))
    assert result.action == "transform"
    assert result.text == "hi!"


# -----------------------------------------------------------------------------
# Project trust 事件
# -----------------------------------------------------------------------------


def _project_trust_ctx() -> ProjectTrustContext:
    return ProjectTrustContext(
        cwd="/tmp",
        mode="print",
        has_ui=False,
        ui=NoOpUIContext(),
    )


@pytest.mark.asyncio
async def test_emit_project_trust_yes_wins():
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "yes"}]},
    )
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_no_wins():
    ext = Extension(
        path="e1",
        handlers={"project_trust": [lambda event, ctx: {"trusted": "no"}]},
    )
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="no", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_skips_undecided():
    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                lambda event, ctx: {"trusted": "undecided"},
                lambda event, ctx: {"trusted": "yes", "remember": True},
            ]
        },
    )
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=True)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_all_undecided_returns_none():
    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                lambda event, ctx: {"trusted": "undecided"},
            ]
        },
    )
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result is None
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_async_handler():
    async def handler(event, ctx):
        return {"trusted": "yes"}

    ext = Extension(path="e1", handlers={"project_trust": [handler]})
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert errors == []


@pytest.mark.asyncio
async def test_emit_project_trust_error_does_not_break_others():
    def bad_handler(event, ctx):
        raise RuntimeError("boom")

    ext = Extension(
        path="e1",
        handlers={
            "project_trust": [
                bad_handler,
                lambda event, ctx: {"trusted": "yes"},
            ]
        },
    )
    runner = _runner([ext])
    result, errors = await runner.emit_project_trust(
        ProjectTrustEvent(cwd="/tmp"), _project_trust_ctx()
    )
    assert result == ProjectTrustEventResult(trusted="yes", remember=False)
    assert len(errors) == 1
    assert errors[0]["extension_path"] == "e1"
    assert "boom" in errors[0]["error"]
