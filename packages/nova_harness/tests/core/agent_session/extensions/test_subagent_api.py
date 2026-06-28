"""
Extension subagent API 单元测试。

验证 ExtensionRunner / NovaExtensionAPI 能正确暴露 create_subagent_session
并复用父 session 的 services。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nova_harness.core.agent_session.extensions import (
    Extension,
    ExtensionRunner,
    NovaExtensionAPI,
)


@pytest.fixture
def services():
    """构造一个 mock 的 AgentSessionServices。"""
    return MagicMock(
        cwd="/tmp",
        agent_dir="/agent_dir",
        auth_storage=MagicMock(),
        settings_manager=MagicMock(),
        model_registry=MagicMock(),
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
    )


@pytest.fixture
def runner(services):
    """构造一个空的 ExtensionRunner。"""
    return ExtensionRunner(services=services, extensions=[])


@pytest.fixture
def api(runner):
    """构造一个 NovaExtensionAPI。"""
    ext = Extension(path="ext/path", name="ext")
    return NovaExtensionAPI(ext, runner)


def _make_options_factory(captured: dict):
    """返回一个轻量工厂，用于替换 CreateAgentSessionOptions 以绕过 Pydantic 校验。"""

    def factory(**kwargs):
        captured["kwargs"] = kwargs
        obj = MagicMock()
        for key, value in kwargs.items():
            setattr(obj, key, value)
        return obj

    return factory


@pytest.mark.asyncio
async def test_extension_api_create_subagent_session_delegates_to_context(api, runner):
    """NovaExtensionAPI.create_subagent_session 委托给 ExtensionRunner。"""
    runner.create_subagent_session = AsyncMock(return_value="runtime")
    result = await api.create_subagent_session("scout")
    assert result == "runtime"
    runner.create_subagent_session.assert_awaited_once_with("scout", None)


@pytest.mark.asyncio
async def test_extension_runner_create_subagent_session_reuses_services(services):
    """create_subagent_session 复用父 session 的 services。"""
    runner = ExtensionRunner(services=services, extensions=[])
    mock_runtime = MagicMock()
    captured: dict = {}

    with (
        patch(
            "nova_harness.core.sdk.CreateAgentSessionOptions",
            new=_make_options_factory(captured),
        ),
        patch(
            "nova_harness.core.sdk.create_agent_session",
            new=AsyncMock(return_value=mock_runtime),
        ) as mock_create,
    ):
        result = await runner.create_subagent_session("scout")

    assert result is mock_runtime
    mock_create.assert_awaited_once()
    opts = captured["kwargs"]
    assert opts["agent_name"] == "scout"
    assert opts["agent_dir"] == "/agent_dir"
    assert opts["auth_storage"] is services.auth_storage
    assert opts["model_registry"] is services.model_registry
    assert opts["settings_manager"] is services.settings_manager
    assert opts["resource_loader"] is services.resource_loader
    assert opts["system_prompt_manager"] is services.system_prompt_manager


@pytest.mark.asyncio
async def test_extension_runner_create_subagent_session_passes_options(services):
    """create_subagent_session 正确透传 cwd/model/thinking_level。"""
    runner = ExtensionRunner(services=services, extensions=[])
    captured: dict = {}

    options = MagicMock()
    options.cwd = "/subagent/cwd"
    options.model = "test-model"
    options.thinking_level = "low"

    with (
        patch(
            "nova_harness.core.sdk.CreateAgentSessionOptions",
            new=_make_options_factory(captured),
        ),
        patch(
            "nova_harness.core.sdk.create_agent_session",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        await runner.create_subagent_session("planner", options)

    opts = captured["kwargs"]
    assert opts["cwd"] == "/subagent/cwd"
    assert opts["model"] == "test-model"
    assert opts["thinking_level"] == "low"


@pytest.mark.asyncio
async def test_extension_runner_create_subagent_session_defaults_to_parent_cwd(
    services,
):
    """未提供 cwd 时默认使用父 session 的 cwd。"""
    runner = ExtensionRunner(services=services, extensions=[])
    captured: dict = {}

    with (
        patch(
            "nova_harness.core.sdk.CreateAgentSessionOptions",
            new=_make_options_factory(captured),
        ),
        patch(
            "nova_harness.core.sdk.create_agent_session",
            new=AsyncMock(return_value=MagicMock()),
        ),
    ):
        await runner.create_subagent_session("worker")

    opts = captured["kwargs"]
    assert opts["cwd"] == "/tmp"
