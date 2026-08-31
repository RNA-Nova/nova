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
        get_personas=lambda: [],
        get_persona_override=lambda: None,
        set_persona_override=lambda name: None,
        clear_persona_override=lambda: None,
        get_agents=lambda: [],
        change_agent=lambda name: None,
        save_agent=lambda as_name=None: None,
        get_executor_settings=lambda: None,
        register_executor_endpoint=lambda *args: None,
        unregister_executor_endpoint=lambda *args: False,
        refresh_system_prompt=lambda: None,
    )
    return runtime


def _runner(extensions):
    return ExtensionRunner(
        extensions=extensions,
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )


# -----------------------------------------------------------------------------
# 命令注册与自动重命名
# -----------------------------------------------------------------------------


def test_get_registered_commands_unique_unchanged():
    runner = _runner(
        [Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})]
    )
    cmds = runner.get_registered_commands()
    assert [c.resolved_name for c in cmds] == ["foo"]
    assert [c.name for c in cmds] == ["foo"]


def test_get_registered_commands_duplicates_renamed():
    """同名命令按 TS resolveRegisteredCommands 行为生成 name:1、name:2 ..."""
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    ext3 = Extension(path="e3", commands={"foo": RegisteredCommand(name="foo")})
    runner = _runner([ext1, ext2, ext3])
    cmds = runner.get_registered_commands()
    assert [c.resolved_name for c in cmds] == ["foo:1", "foo:2", "foo:3"]
    # 原始名保留
    assert [c.name for c in cmds] == ["foo", "foo", "foo"]


def test_get_registered_commands_collision_with_existing_name():
    """自动生成的调用名与后续原始命令名冲突时递增避让。"""
    ext1 = Extension(path="e1", commands={"foo": RegisteredCommand(name="foo")})
    ext2 = Extension(path="e2", commands={"foo": RegisteredCommand(name="foo")})
    ext3 = Extension(path="e3", commands={"foo:2": RegisteredCommand(name="foo:2")})
    runner = _runner([ext1, ext2, ext3])
    cmds = runner.get_registered_commands()
    assert [c.resolved_name for c in cmds] == ["foo:1", "foo:2", "foo:2:2"]
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
    img = {"type": "image", "mime_type": "image/png", "data": "aGVsbG8="}

    def handler(event, ctx):
        return InputEventResult(action="transform", images=[img])

    ext = Extension(path="e1", handlers={INPUT: [handler]})
    runner = _runner([ext])
    result = await runner.emit_input(InputEvent(text="hello"))
    assert result.action == "transform"
    # InputEventResult 经模型校验——images 元素是 ImageContent 实例
    assert len(result.images) == 1
    assert result.images[0].mime_type == "image/png"
    assert result.images[0].data == "aGVsbG8="


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
        model_runtime=None,
    )
    result = await runner.emit_input(InputEvent(text="hi"))
    assert result.action == "transform"
    assert result.text == "hi!"


# -----------------------------------------------------------------------------
# session_before_* 事件的结果收集
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_before_emit_returns_last_non_none_result():
    """后面的 handler 返回 None 时，不覆盖前面已收集的非 None 结果（对齐 TS）。"""

    def handler_with_result(event, ctx):
        return {"compaction": {"summary": "s"}}

    def handler_none(event, ctx):
        return None

    ext_a = Extension(
        path="a", handlers={"session_before_compact": [handler_with_result]}
    )
    ext_b = Extension(path="b", handlers={"session_before_compact": [handler_none]})

    runner = _runner([ext_a, ext_b])
    event = SimpleNamespace(type="session_before_compact")
    result = await runner.emit(event)

    assert result == {"compaction": {"summary": "s"}}


@pytest.mark.asyncio
async def test_session_before_emit_cancel_short_circuits():
    """cancel=True 立即返回，不再执行后续 handler（对齐 TS）。"""
    calls = []

    def handler_cancel(event, ctx):
        calls.append("cancel")
        return {"cancel": True}

    def handler_after(event, ctx):
        calls.append("after")
        return {"compaction": {}}

    ext_a = Extension(path="a", handlers={"session_before_compact": [handler_cancel]})
    ext_b = Extension(path="b", handlers={"session_before_compact": [handler_after]})

    runner = _runner([ext_a, ext_b])
    result = await runner.emit(SimpleNamespace(type="session_before_compact"))

    assert result == {"cancel": True}
    assert calls == ["cancel"]


@pytest.mark.asyncio
async def test_non_session_before_emit_returns_none():
    """非 session_before_* 事件的通用 emit 只分发不返回结果。"""

    def handler(event, ctx):
        return {"anything": True}

    ext = Extension(path="a", handlers={"agent_start": [handler]})
    runner = _runner([ext])
    result = await runner.emit(SimpleNamespace(type="agent_start"))

    assert result is None


# -----------------------------------------------------------------------------
# emit_tool_call 的 fail-closed 语义
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_tool_call_handler_error_blocks_execution():
    """tool_call handler 抛异常时 fail-closed：返回 block 而非放行。

    拦截类扩展（permission gate 等）崩溃若静默放行，危险操作会径直穿过
    门禁——异常必须表现为拒绝（对齐 TS 的 tool_call 钩子语义）。
    """
    from nova_harness.core.types.events import ToolCallEvent
    from nova_harness.core.types.events.constants import TOOL_CALL

    def boom(event, ctx):
        raise RuntimeError("gate crashed")

    ext = Extension(path="gate", handlers={TOOL_CALL: [boom]})
    runner = _runner([ext])
    errors = []
    runner.on_error(errors.append)

    result = await runner.emit_tool_call(ToolCallEvent(tool_name="bash", args={}))

    assert result is not None
    assert result.block is True
    assert "Extension failed, blocking execution" in (result.reason or "")
    assert "gate crashed" in (result.reason or "")
    assert errors and errors[0]["event"] == TOOL_CALL


@pytest.mark.asyncio
async def test_emit_tool_call_handler_error_skips_remaining_handlers():
    """fail-closed 短路：异常后不再执行后续 handler（对齐 TS 的短路语义）。"""
    from nova_harness.core.types.events import ToolCallEvent
    from nova_harness.core.types.events.constants import TOOL_CALL

    calls = []

    def boom(event, ctx):
        raise RuntimeError("gate crashed")

    def after(event, ctx):
        calls.append("after")

    ext = Extension(path="gate", handlers={TOOL_CALL: [boom, after]})
    runner = _runner([ext])

    result = await runner.emit_tool_call(ToolCallEvent(tool_name="bash", args={}))

    assert result is not None and result.block is True
    assert calls == []


# -----------------------------------------------------------------------------
# emit_before_provider_headers（pi 对齐：原地改、fail-open）
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_before_provider_headers_in_place_mutation():
    """handler 原地修改 headers，返回的同一 dict 带修改；返回值被忽略。"""
    from nova_harness.core.types.events.constants import BEFORE_PROVIDER_HEADERS

    def handler(event, ctx):
        event.headers["x-ext"] = "1"
        return "ignored"

    ext = Extension(path="ext", handlers={BEFORE_PROVIDER_HEADERS: [handler]})
    runner = _runner([ext])

    headers = {"authorization": "Bearer k"}
    result = await runner.emit_before_provider_headers(headers)

    assert result is headers
    assert result == {"authorization": "Bearer k", "x-ext": "1"}


@pytest.mark.asyncio
async def test_emit_before_provider_headers_chain_visibility():
    """串行 handler 后者可见前者的修改。"""
    from nova_harness.core.types.events.constants import BEFORE_PROVIDER_HEADERS

    def first(event, ctx):
        event.headers["x-first"] = "a"

    seen = {}

    def second(event, ctx):
        seen.update(event.headers)

    ext_a = Extension(path="a", handlers={BEFORE_PROVIDER_HEADERS: [first]})
    ext_b = Extension(path="b", handlers={BEFORE_PROVIDER_HEADERS: [second]})
    runner = _runner([ext_a, ext_b])

    await runner.emit_before_provider_headers({})
    assert seen == {"x-first": "a"}


@pytest.mark.asyncio
async def test_emit_before_provider_headers_fail_open():
    """handler 异常 fail-open：转 error 事件、请求继续、后续 handler 仍执行。"""
    from nova_harness.core.types.events.constants import BEFORE_PROVIDER_HEADERS

    def boom(event, ctx):
        raise RuntimeError("bad handler")

    def still_runs(event, ctx):
        event.headers["x-after"] = "ok"

    ext = Extension(path="bad", handlers={BEFORE_PROVIDER_HEADERS: [boom]})
    ext2 = Extension(path="good", handlers={BEFORE_PROVIDER_HEADERS: [still_runs]})
    runner = _runner([ext, ext2])
    errors = []
    runner.on_error(errors.append)

    result = await runner.emit_before_provider_headers({})

    assert result == {"x-after": "ok"}
    assert errors and errors[0]["event"] == BEFORE_PROVIDER_HEADERS


@pytest.mark.asyncio
async def test_emit_before_provider_headers_no_handlers():
    runner = _runner([])
    headers = {"a": "b"}
    assert await runner.emit_before_provider_headers(headers) is headers
