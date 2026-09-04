"""before_provider_headers 的 stream_fn 接线测试。

``create_stream_fn`` 经 ``transform_headers`` 把扩展的 headers 钩子挂进
provider 请求链：有 runner + handler 时改写生效，无 runner 时 transform
不安装，既有 transform_headers 被链接而非覆盖。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import nova_harness.core.agent_session.factory as factory_module
import pytest
from nova_ai import Model
from nova_harness.core.agent_session.factory import create_stream_fn
from nova_harness.core.extensions.runner import ExtensionRunner
from nova_harness.core.types.events.constants import BEFORE_PROVIDER_HEADERS
from nova_harness.core.types.extensions import Extension, ExtensionRuntime


def _minimal_runtime() -> ExtensionRuntime:
    runtime = ExtensionRuntime(cwd="/tmp")
    for name in (
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
    ):
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
        refresh_system_prompt=lambda: None,
    )
    return runtime


def _services():
    services = MagicMock()
    services.settings_manager.get_retry_settings.return_value = SimpleNamespace(
        provider=None
    )
    services.model_runtime.stream_simple = MagicMock(return_value="stream")
    return services


def _runner_with_headers_hook():
    def handler(event, ctx):
        event.headers["x-ext"] = "injected"

    ext = Extension(path="ext", handlers={BEFORE_PROVIDER_HEADERS: [handler]})
    return ExtensionRunner(
        extensions=[ext],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )


def _captured_options(services):
    args, _ = services.model_runtime.stream_simple.call_args
    return args[2]


@pytest.mark.asyncio
async def test_stream_fn_wires_headers_hook(monkeypatch):
    """有 runner + handler：options.transform_headers 应用扩展改写。"""
    monkeypatch.setattr(
        factory_module, "merge_provider_attribution_headers", lambda *a: None
    )
    services = _services()
    ref = {"current": _runner_with_headers_hook()}

    stream_fn = create_stream_fn(services, "sess-1", ref)
    model = MagicMock(spec=Model)
    model.headers = None
    await stream_fn(model, None, None)

    options = _captured_options(services)
    assert options.transform_headers is not None
    headers = await options.transform_headers({"authorization": "Bearer k"})
    assert headers == {"authorization": "Bearer k", "x-ext": "injected"}


@pytest.mark.asyncio
async def test_stream_fn_no_runner_no_transform(monkeypatch):
    """无 runner ref：不安装 transform_headers。"""
    monkeypatch.setattr(
        factory_module, "merge_provider_attribution_headers", lambda *a: None
    )
    services = _services()

    stream_fn = create_stream_fn(services, "sess-1", None)
    model = MagicMock(spec=Model)
    model.headers = None
    await stream_fn(model, None, None)

    options = _captured_options(services)
    assert options.transform_headers is None


@pytest.mark.asyncio
async def test_stream_fn_chains_existing_transform(monkeypatch):
    """调用方已有 transform_headers 时链接：先跑既有，再跑扩展钩子。"""
    from nova_ai.types.stream_options import SimpleStreamOptions

    monkeypatch.setattr(
        factory_module, "merge_provider_attribution_headers", lambda *a: None
    )
    services = _services()
    ref = {"current": _runner_with_headers_hook()}

    order = []

    def existing(headers):
        order.append("existing")
        return {**headers, "x-base": "yes"}

    stream_fn = create_stream_fn(services, "sess-1", ref)
    model = MagicMock(spec=Model)
    model.headers = None
    options = SimpleStreamOptions(transform_headers=existing)
    await stream_fn(model, None, options)

    options = _captured_options(services)
    headers = await options.transform_headers({})
    assert order == ["existing"]
    assert headers == {"x-base": "yes", "x-ext": "injected"}


@pytest.mark.asyncio
async def test_stream_fn_runner_without_handlers_passes_through(monkeypatch):
    """runner 无 headers handler：transform 原样返回 headers。"""
    monkeypatch.setattr(
        factory_module, "merge_provider_attribution_headers", lambda *a: None
    )
    services = _services()
    runner = ExtensionRunner(
        extensions=[],
        runtime=_minimal_runtime(),
        cwd="/tmp",
        session_manager=None,
        model_runtime=None,
    )
    ref = {"current": runner}

    stream_fn = create_stream_fn(services, "sess-1", ref)
    model = MagicMock(spec=Model)
    model.headers = None
    await stream_fn(model, None, None)

    options = _captured_options(services)
    headers = {"a": "b"}
    assert await options.transform_headers(headers) == {"a": "b"}
