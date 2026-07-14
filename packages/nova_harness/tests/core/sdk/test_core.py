"""
sdk/core.py 工厂与解析逻辑测试。
"""

from unittest.mock import MagicMock

import pytest
from nova_ai import Model, ThinkingLevel

from nova_harness.core.config import ModelRegistry
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.model_resolver import (
    find_initial_model,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
    resolve_thinking_level,
    restore_model_from_session,
)
from nova_harness.core.sdk import CreateAgentSessionOptions
from tests._helpers.auth_storage import auth_storage_in_memory
from tests._helpers.settings_manager import settings_manager_in_memory


def _make_services(tmp_path, auth_storage=None, settings=None, models_json=None):
    """构造可预测的 AgentSessionServices。"""
    cwd = str(tmp_path)
    agent_dir = str(tmp_path / "agent")
    auth = auth_storage or auth_storage_in_memory({})
    settings_manager = settings or settings_manager_in_memory()
    model_registry = ModelRegistry(auth, models_json or str(tmp_path / "models.json"))
    session_manager = SessionManager.in_memory(cwd)
    services = MagicMock(
        cwd=cwd,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
        model_registry=model_registry,
        resource_loader=MagicMock(),
        auth_storage=auth,
    )
    services.session_manager = session_manager
    return services


@pytest.mark.asyncio
async def test_find_initial_model_prefers_explicit(tmp_path):
    services = _make_services(tmp_path)
    preferred = MagicMock(spec=Model)
    result = await find_initial_model(
        services,
        preferred_model=preferred,
    )
    assert result.model is preferred
    assert result.fallback_message is None


@pytest.mark.asyncio
async def test_find_initial_model_falls_back_to_settings(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    settings = settings_manager_in_memory(
        {"default_provider": "volcengine", "default_model": "deepseek-v3-2-251201"}
    )
    services = _make_services(tmp_path, auth_storage=auth, settings=settings)
    result = await find_initial_model(services)
    assert result.model is not None
    assert result.model.provider == "volcengine"
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.fallback_message is None


@pytest.mark.asyncio
async def test_find_initial_model_falls_back_to_any_available(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = await find_initial_model(services)
    assert result.model is not None
    assert result.model.provider == "volcengine"
    assert result.fallback_message is None


@pytest.mark.asyncio
async def test_find_initial_model_returns_none_when_no_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    services = _make_services(tmp_path)
    result = await find_initial_model(services)
    assert result.model is None
    assert result.fallback_message is not None


def test_resolve_thinking_level_prefers_explicit():
    services = MagicMock()
    services.session_manager.build_session_context.return_value = MagicMock(
        thinking_level=ThinkingLevel.LOW
    )
    services.session_manager.get_branch.return_value = []
    services.settings_manager.get_default_thinking_level.return_value = (
        ThinkingLevel.MEDIUM
    )
    model = MagicMock()
    model.thinking_level_map = {
        "minimal": None,
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "max",
    }
    result = resolve_thinking_level(
        services, services.session_manager, model, preferred_level=ThinkingLevel.HIGH
    )
    assert result == ThinkingLevel.HIGH


def test_resolve_thinking_level_clamps_to_model_support():
    services = MagicMock()
    services.session_manager.build_session_context.return_value = MagicMock(
        thinking_level=ThinkingLevel.HIGH
    )
    services.session_manager.get_branch.return_value = []
    services.settings_manager.get_default_thinking_level.return_value = (
        ThinkingLevel.MEDIUM
    )

    model = MagicMock()
    model.thinking_level_map = {"minimal": None, "low": "low"}
    result = resolve_thinking_level(services, services.session_manager, model)
    assert result == ThinkingLevel.LOW


def test_resolve_thinking_level_none_maps_to_off():
    services = MagicMock()
    services.session_manager.build_session_context.return_value = MagicMock(
        thinking_level=None
    )
    services.session_manager.get_branch.return_value = [
        MagicMock(type="thinking_level_change")
    ]
    services.settings_manager.get_default_thinking_level.return_value = (
        ThinkingLevel.MEDIUM
    )
    result = resolve_thinking_level(services, services.session_manager, MagicMock())
    assert result is None


def test_create_agent_session_options_defaults():
    opts = CreateAgentSessionOptions()
    assert opts.cwd is None
    assert opts.model is None
    assert opts.tools is None


@pytest.mark.asyncio
async def test_resolve_cli_model_provider_slash_pattern(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = resolve_cli_model(
        cli_provider="volcengine",
        cli_model="deepseek-v3-2-251201",
        model_registry=services.model_registry,
    )
    assert result.model is not None
    assert result.model.provider == "volcengine"
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.error is None


@pytest.mark.asyncio
async def test_resolve_cli_model_fuzzy_prefix(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = resolve_cli_model(
        cli_provider=None,
        cli_model="deepseek",
        model_registry=services.model_registry,
    )
    assert result.model is not None
    assert result.model.provider == "volcengine"
    assert "deepseek" in result.model.id


@pytest.mark.asyncio
async def test_resolve_cli_model_unknown_provider(tmp_path):
    services = _make_services(tmp_path)
    result = resolve_cli_model(
        cli_provider="unknown-provider",
        cli_model="foo",
        model_registry=services.model_registry,
    )
    assert result.model is None
    assert result.error is not None
    assert "unknown-provider" in result.error


def test_parse_model_pattern_extracts_thinking_level():
    from nova_ai import get_model

    available = [get_model("volcengine", "deepseek-v3-2-251201")]
    result = parse_model_pattern(
        "deepseek-v3-2-251201:high",
        available,
    )
    assert result.model is not None
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.thinking_level == ThinkingLevel.HIGH


def test_parse_model_pattern_invalid_thinking_level_warns():
    from nova_ai import get_model

    available = [get_model("volcengine", "deepseek-v3-2-251201")]
    result = parse_model_pattern(
        "deepseek-v3-2-251201:invalid",
        available,
    )
    assert result.model is not None
    assert result.thinking_level is None
    assert result.warning is not None
    assert "Invalid thinking level" in result.warning


@pytest.mark.asyncio
async def test_resolve_model_scope_with_glob(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    scoped = resolve_model_scope(
        ["volcengine/*"],
        services.model_registry,
    )
    assert len(scoped) >= 1
    assert all(sm.model.provider == "volcengine" for sm in scoped)


@pytest.mark.asyncio
async def test_restore_model_from_session_uses_current_on_auth_failure(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    current = services.model_registry.find("volcengine", "deepseek-v3-2-251201")
    result = await restore_model_from_session(
        saved_provider="volcengine",
        saved_model_id="non-existent-model",
        current_model=current,
        model_registry=services.model_registry,
    )
    assert result.model is not None
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.fallback_message is not None
