"""
sdk/core.py 工厂与解析逻辑测试。
"""

from unittest.mock import MagicMock

import pytest
from nova_ai import Model, ThinkingLevel

from nova_harness.core.config import AuthStorage, ModelRegistry, SettingsManager
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.sdk import (
    CreateAgentSessionOptions,
    _resolve_initial_model,
    _resolve_thinking_level,
)


def _make_services(tmp_path, auth_storage=None, settings=None, models_json=None):
    """构造可预测的 AgentSessionServices。"""
    cwd = str(tmp_path)
    agent_dir = str(tmp_path / "agent")
    auth = auth_storage or AuthStorage.in_memory({})
    settings_manager = settings or SettingsManager.in_memory()
    model_registry = ModelRegistry(auth, models_json or str(tmp_path / "models.json"))
    session_manager = SessionManager.in_memory(cwd)
    return MagicMock(
        cwd=cwd,
        agent_dir=agent_dir,
        session_manager=session_manager,
        settings_manager=settings_manager,
        model_registry=model_registry,
        resource_loader=MagicMock(),
        system_prompt_manager=MagicMock(),
        auth_storage=auth,
    )


@pytest.mark.asyncio
async def test_resolve_initial_model_prefers_explicit(tmp_path):
    services = _make_services(tmp_path)
    preferred = MagicMock(spec=Model)
    result = await _resolve_initial_model(services, preferred_model=preferred)
    assert result is preferred


@pytest.mark.asyncio
async def test_resolve_initial_model_falls_back_to_settings(tmp_path):
    auth = AuthStorage.in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    settings = SettingsManager.in_memory(
        {"default_provider": "volcengine", "default_model": "deepseek-v3-2-251201"}
    )
    services = _make_services(tmp_path, auth_storage=auth, settings=settings)
    result = await _resolve_initial_model(services)
    assert result is not None
    assert result.provider == "volcengine"
    assert result.id == "deepseek-v3-2-251201"


@pytest.mark.asyncio
async def test_resolve_initial_model_falls_back_to_any_available(tmp_path):
    auth = AuthStorage.in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = await _resolve_initial_model(services)
    assert result is not None
    assert result.provider == "volcengine"


@pytest.mark.asyncio
async def test_resolve_initial_model_returns_none_when_no_auth(tmp_path, monkeypatch):
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    services = _make_services(tmp_path)
    result = await _resolve_initial_model(services)
    assert result is None


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
    result = _resolve_thinking_level(
        services, model, preferred_level=ThinkingLevel.HIGH
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
    result = _resolve_thinking_level(services, model)
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
    result = _resolve_thinking_level(services, MagicMock())
    assert result is None


def test_create_agent_session_options_defaults():
    opts = CreateAgentSessionOptions()
    assert opts.cwd is None
    assert opts.model is None
    assert opts.tools is None
