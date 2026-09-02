"""
sdk/core.py 工厂与解析逻辑测试。
"""

from unittest.mock import MagicMock

import pytest
from nova_ai import Model, ModelThinkingLevel
from nova_harness.core.harness.session import SessionManager
from nova_harness.core.model import ModelRuntime
from nova_harness.core.model.resolver import (
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
    model_runtime = ModelRuntime(auth, models_json or str(tmp_path / "models.json"))
    session_manager = SessionManager.in_memory(cwd)
    services = MagicMock(
        cwd=cwd,
        agent_dir=agent_dir,
        settings_manager=settings_manager,
        model_runtime=model_runtime,
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
        thinking_level=ModelThinkingLevel.LOW
    )
    services.session_manager.get_branch.return_value = []
    services.settings_manager.get_default_thinking_level.return_value = (
        ModelThinkingLevel.MEDIUM
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
        services,
        services.session_manager,
        model,
        preferred_level=ModelThinkingLevel.HIGH,
    )
    assert result == ModelThinkingLevel.HIGH


def test_resolve_thinking_level_clamps_to_model_support():
    services = MagicMock()
    services.session_manager.build_session_context.return_value = MagicMock(
        thinking_level=ModelThinkingLevel.HIGH
    )
    services.session_manager.get_branch.return_value = []
    services.settings_manager.get_default_thinking_level.return_value = (
        ModelThinkingLevel.MEDIUM
    )

    model = MagicMock()
    # medium/high 显式禁用：默认 MEDIUM 先向上吸附（high 不可用），再向下命中 low
    model.thinking_level_map = {
        "minimal": None,
        "low": "low",
        "medium": None,
        "high": None,
    }
    result = resolve_thinking_level(services, services.session_manager, model)
    assert result == ModelThinkingLevel.LOW


def test_resolve_thinking_level_none_maps_to_off():
    services = MagicMock()
    services.session_manager.build_session_context.return_value = MagicMock(
        thinking_level=None
    )
    services.session_manager.get_branch.return_value = [
        MagicMock(type="thinking_level_change")
    ]
    services.settings_manager.get_default_thinking_level.return_value = (
        ModelThinkingLevel.MEDIUM
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
        model_runtime=services.model_runtime,
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
        model_runtime=services.model_runtime,
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
        model_runtime=services.model_runtime,
    )
    assert result.model is None
    assert result.error is not None
    assert "unknown-provider" in result.error


def test_parse_model_pattern_extracts_thinking_level():
    from nova_ai import get_builtin_model

    available = [get_builtin_model("volcengine", "deepseek-v3-2-251201")]
    result = parse_model_pattern(
        "deepseek-v3-2-251201:high",
        available,
    )
    assert result.model is not None
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.thinking_level == ModelThinkingLevel.HIGH


def test_parse_model_pattern_invalid_thinking_level_warns():
    from nova_ai import get_builtin_model

    available = [get_builtin_model("volcengine", "deepseek-v3-2-251201")]
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
        services.model_runtime,
    )
    assert len(scoped) >= 1
    assert all(sm.model.provider == "volcengine" for sm in scoped)


@pytest.mark.asyncio
async def test_restore_model_from_session_uses_current_on_auth_failure(tmp_path):
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    current = services.model_runtime.find("volcengine", "deepseek-v3-2-251201")
    result = await restore_model_from_session(
        saved_provider="volcengine",
        saved_model_id="non-existent-model",
        current_model=current,
        model_runtime=services.model_runtime,
    )
    assert result.model is not None
    assert result.model.id == "deepseek-v3-2-251201"
    assert result.fallback_message is not None


# ---------------------------------------------------------------------------
# find_initial_model：agent 组合声明 model（tier 4——CLI/scoped 之后、settings 之前）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_initial_model_agent_model_beats_settings_default(tmp_path):
    """agent yaml 的 model 命中（有鉴权）时胜过 settings 默认模型。"""
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    settings = settings_manager_in_memory(
        {"default_provider": "volcengine", "default_model": "deepseek-v3-2-251201"}
    )
    services = _make_services(tmp_path, auth_storage=auth, settings=settings)
    result = await find_initial_model(
        services, agent_model="volcengine/deepseek-v4-flash-260425"
    )
    assert result.model is not None
    assert result.model.provider == "volcengine"
    assert result.model.id == "deepseek-v4-flash-260425"


@pytest.mark.asyncio
async def test_find_initial_model_cli_beats_agent_model(tmp_path):
    """CLI 显式模型（tier 2）优先于 agent yaml model（tier 4）。"""
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = await find_initial_model(
        services,
        cli_model="volcengine/deepseek-v4-pro-260425",
        agent_model="volcengine/deepseek-v4-flash-260425",
    )
    assert result.model is not None
    assert result.model.id == "deepseek-v4-pro-260425"


@pytest.mark.asyncio
async def test_find_initial_model_agent_model_without_auth_falls_through(
    tmp_path, monkeypatch
):
    """agent yaml model 无鉴权时静默落回 settings 默认（不硬失败）。"""
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    auth = auth_storage_in_memory({})
    # 只给 settings 默认模型用到的 provider 之外的鉴权无关路径：两个模型同
    # provider，均无 key —— 用 settings 默认也无 key 则继续落到任意可用；
    # 这里验证 agent_model 不会在无 key 时被选中。
    settings = settings_manager_in_memory(
        {"default_provider": "volcengine", "default_model": "deepseek-v3-2-251201"}
    )
    services = _make_services(tmp_path, auth_storage=auth, settings=settings)
    result = await find_initial_model(
        services, agent_model="volcengine/deepseek-v4-flash-260425"
    )
    # 无任何鉴权：最终无可用模型，但关键行为是 agent_model 未短路返回
    assert result.model is None or result.model.id != "deepseek-v4-flash-260425"


@pytest.mark.asyncio
async def test_find_initial_model_unknown_provider_agent_model_falls_through(tmp_path):
    """agent yaml model 写了未知 provider：落回 settings 默认。"""
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    settings = settings_manager_in_memory(
        {"default_provider": "volcengine", "default_model": "deepseek-v3-2-251201"}
    )
    services = _make_services(tmp_path, auth_storage=auth, settings=settings)
    result = await find_initial_model(services, agent_model="ghost-provider/some-model")
    assert result.model is not None
    assert result.model.id == "deepseek-v3-2-251201"


@pytest.mark.asyncio
async def test_find_initial_model_custom_agent_model_id_carries_warning(tmp_path):
    """已知 provider 的未注册模型 id：按 CLI 语义建自定义模型并透出警告。"""
    auth = auth_storage_in_memory({})
    auth.set_runtime_api_key("volcengine", "key")
    services = _make_services(tmp_path, auth_storage=auth)
    result = await find_initial_model(
        services, agent_model="volcengine/nonexistent-model"
    )
    assert result.model is not None
    assert result.model.id == "nonexistent-model"
    assert result.fallback_message is not None
    assert "custom model id" in result.fallback_message
