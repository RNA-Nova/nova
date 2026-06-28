"""
ModelController 单元测试。

验证模型切换、思考级别循环、鉴权检查与事件发射。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nova_ai import Model, ThinkingLevel

from nova_harness.core.agent_session.controllers.model import (
    ModelController,
    _thinking_level_from_value,
)
from nova_harness.core.types.agent import ModelCycleResult
from nova_harness.core.types.events import (
    ModelSelectEvent,
    ThinkingLevelChangedEvent,
    ThinkingLevelSelectEvent,
)


@pytest.fixture
def model_session(make_agent_session):
    """构造一个用于测试 ModelController 的 session。"""
    return make_agent_session()


def _make_model(name="m1", provider="p1"):
    return Model.model_construct(id=name, provider=provider, reasoning=True, api=None)


# ---------------------------------------------------------------------------
# _thinking_level_from_value
# ---------------------------------------------------------------------------


def test_thinking_level_from_value_none():
    """None 映射为 None。"""
    assert _thinking_level_from_value(None) is None


def test_thinking_level_from_value_none_strings():
    """none/off/空字符串映射为 None。"""
    assert _thinking_level_from_value("none") is None
    assert _thinking_level_from_value("off") is None
    assert _thinking_level_from_value("") is None


def test_thinking_level_from_value_valid():
    """合法字符串与枚举直接返回。"""
    assert _thinking_level_from_value("low") == ThinkingLevel.LOW
    assert _thinking_level_from_value(ThinkingLevel.HIGH) == ThinkingLevel.HIGH


def test_thinking_level_from_value_invalid_defaults_to_medium():
    """非法值回退到 MEDIUM。"""
    assert _thinking_level_from_value("unknown") == ThinkingLevel.MEDIUM


# ---------------------------------------------------------------------------
# emit_model_select / set_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emit_model_select_skips_equal_models(model_session):
    """模型未变化时不发射事件。"""
    model_session._extension_runner = MagicMock()
    m = _make_model()
    await model_session._model.emit_model_select(m, m, "set")
    model_session._extension_runner.emit.assert_not_called()


@pytest.mark.asyncio
async def test_emit_model_select_emits_event(model_session):
    """模型变化时发射 ModelSelectEvent。"""
    runner = MagicMock()
    runner.emit = AsyncMock()
    model_session._extension_runner = runner
    prev = _make_model("m0")
    nxt = _make_model("m1")
    await model_session._model.emit_model_select(nxt, prev, "cycle")
    runner.emit.assert_awaited_once()
    ev = runner.emit.call_args[0][0]
    assert isinstance(ev, ModelSelectEvent)
    assert ev.model is nxt


@pytest.mark.asyncio
async def test_set_model_raises_without_api_key(model_session):
    """缺少 API key 时 set_model 应抛错。"""
    model_session.model_registry.get_api_key = AsyncMock(return_value="")
    with pytest.raises(RuntimeError, match="No API key"):
        await model_session.set_model(_make_model())


@pytest.mark.asyncio
async def test_set_model_updates_state_and_settings(model_session):
    """set_model 应更新 agent state、session 与设置。"""
    new_model = _make_model("new", "volcengine")
    await model_session.set_model(new_model)

    assert model_session.agent.state.model is new_model
    model_session.session_manager.append_model_change.assert_called_once_with(
        "volcengine", "new"
    )
    model_session.settings_manager.set_default_model_and_provider.assert_called_once_with(
        "volcengine", "new"
    )


# ---------------------------------------------------------------------------
# cycle_model
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_scoped_model_forward(model_session):
    """scoped_models 中向前循环。"""
    m1 = _make_model("m1")
    m2 = _make_model("m2")
    model_session.scoped_models = [
        SimpleNamespace(model=m1, thinking_level=ThinkingLevel.LOW),
        SimpleNamespace(model=m2, thinking_level=ThinkingLevel.HIGH),
    ]
    model_session.agent.state.model = m1

    with patch.object(model_session._model, "_model_has_auth", return_value=True):
        result = await model_session._model.cycle_model("forward")

    assert isinstance(result, ModelCycleResult)
    assert result.model is m2
    assert result.thinking_level == ThinkingLevel.HIGH
    assert result.is_scoped is True


@pytest.mark.asyncio
async def test_cycle_scoped_model_backward(model_session):
    """scoped_models 中向后循环。"""
    m1 = _make_model("m1")
    m2 = _make_model("m2")
    model_session.scoped_models = [
        SimpleNamespace(model=m1, thinking_level=ThinkingLevel.LOW),
        SimpleNamespace(model=m2, thinking_level=ThinkingLevel.HIGH),
    ]
    model_session.agent.state.model = m1

    with patch.object(model_session._model, "_model_has_auth", return_value=True):
        result = await model_session._model.cycle_model("backward")

    assert result.model is m2


@pytest.mark.asyncio
async def test_cycle_scoped_model_returns_none_if_too_few(model_session):
    """scoped_models 数量不足时返回 None。"""
    m1 = _make_model("m1")
    model_session.scoped_models = [SimpleNamespace(model=m1)]
    model_session.agent.state.model = m1
    with patch.object(model_session._model, "_model_has_auth", return_value=True):
        assert await model_session._model.cycle_model("forward") is None


@pytest.mark.asyncio
async def test_cycle_available_model(model_session):
    """没有 scoped_models 时在所有可用模型中循环。"""
    m1 = _make_model("m1")
    m2 = _make_model("m2")
    model_session.agent.state.model = m1
    model_session.model_registry.get_available.return_value = [m1, m2]

    result = await model_session._model.cycle_model("forward")

    assert result.model is m2
    assert result.is_scoped is False


@pytest.mark.asyncio
async def test_cycle_available_model_returns_none_if_too_few(model_session):
    """可用模型数量不足时返回 None。"""
    m1 = _make_model("m1")
    model_session.agent.state.model = m1
    model_session.model_registry.get_available.return_value = [m1]
    assert await model_session._model.cycle_model("forward") is None


# ---------------------------------------------------------------------------
# _model_has_auth
# ---------------------------------------------------------------------------


def test_model_has_auth_with_configured_auth():
    """has_configured_auth 优先使用。"""
    sess = SimpleNamespace(
        model_registry=SimpleNamespace(
            has_configured_auth=MagicMock(return_value=False)
        )
    )
    assert ModelController(sess)._model_has_auth(_make_model()) is False


def test_model_has_auth_async_key_returns_true():
    """异步 get_api_key 在同步上下文中保守认为有 auth。"""

    async def async_get_api_key(model):
        return ""

    sess = SimpleNamespace(
        model_registry=SimpleNamespace(get_api_key=async_get_api_key)
    )
    assert ModelController(sess)._model_has_auth(_make_model()) is True


def test_model_has_auth_sync_key():
    """同步 get_api_key 按返回值判断。"""
    sess = SimpleNamespace(
        model_registry=SimpleNamespace(get_api_key=MagicMock(return_value="key"))
    )
    assert ModelController(sess)._model_has_auth(_make_model()) is True


def test_model_has_auth_no_registry():
    """没有 registry 时认为有 auth。"""
    sess = SimpleNamespace(model_registry=None)
    assert ModelController(sess)._model_has_auth(_make_model()) is True


# ---------------------------------------------------------------------------
# set_thinking_level / cycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_thinking_level_same_value_no_event(model_session):
    """思考级别未变化时不发射事件。"""
    model_session.agent.state.thinking_level = ThinkingLevel.MEDIUM
    events = []
    model_session.subscribe(events.append)
    await model_session.set_thinking_level(ThinkingLevel.MEDIUM)
    assert not any(isinstance(e, ThinkingLevelChangedEvent) for e in events)


@pytest.mark.asyncio
async def test_set_thinking_level_changes_and_persists(model_session):
    """思考级别变化时应更新状态、持久化并发射事件。"""
    runner = MagicMock()
    runner.emit = AsyncMock()
    model_session._extension_runner = runner
    model_session.agent.state.thinking_level = ThinkingLevel.LOW
    events = []
    model_session.subscribe(events.append)

    await model_session.set_thinking_level("high")

    assert model_session.agent.state.thinking_level == ThinkingLevel.HIGH
    model_session.session_manager.append_thinking_level_change.assert_called_once_with(
        ThinkingLevel.HIGH
    )
    model_session.settings_manager.set_default_thinking_level.assert_called_once_with(
        ThinkingLevel.HIGH
    )
    assert any(isinstance(e, ThinkingLevelChangedEvent) for e in events)
    runner.emit.assert_awaited_once()
    ev = runner.emit.call_args[0][0]
    assert isinstance(ev, ThinkingLevelSelectEvent)
    assert ev.level == ThinkingLevel.HIGH
    assert ev.previous_level == ThinkingLevel.LOW


@pytest.mark.asyncio
async def test_set_thinking_level_none_turns_off(model_session):
    """设置 none 应关闭思考级别。"""
    model_session.agent.state.thinking_level = ThinkingLevel.MEDIUM
    await model_session.set_thinking_level("none")
    assert model_session.agent.state.thinking_level is None


def test_supports_thinking(model_session):
    """supports_thinking 应读取 model.reasoning。"""
    model_session.agent.state.model = SimpleNamespace(reasoning=True)
    assert model_session._model.supports_thinking() is True
    model_session.agent.state.model = SimpleNamespace(reasoning=False)
    assert model_session._model.supports_thinking() is False


def test_get_available_thinking_levels(model_session):
    """get_available_thinking_levels 应委托给工具函数。"""
    m = SimpleNamespace(reasoning=True)
    model_session.agent.state.model = m
    with patch(
        "nova_harness.core.utils.model_utils.get_supported_thinking_levels",
        return_value=[ThinkingLevel.LOW, ThinkingLevel.HIGH],
    ):
        levels = model_session._model.get_available_thinking_levels()
    assert levels == [ThinkingLevel.LOW, ThinkingLevel.HIGH]


@pytest.mark.asyncio
async def test_cycle_thinking_level_returns_next_and_creates_task(model_session):
    """cycle_thinking_level 应返回下一级别并异步设置。"""
    model_session.agent.state.model = SimpleNamespace(reasoning=True)
    model_session.agent.state.thinking_level = ThinkingLevel.LOW
    with (
        patch(
            "nova_harness.core.utils.model_utils.get_supported_thinking_levels",
            return_value=["low", "high"],
        ),
        patch(
            "nova_harness.core.agent_session.controllers.model.asyncio.create_task"
        ) as mock_create_task,
    ):
        result = model_session._model.cycle_thinking_level()
    assert result == ThinkingLevel.HIGH
    mock_create_task.assert_called_once()
    # 关闭因 create_task 被 patch 而未被调度的协程，避免 RuntimeWarning
    mock_create_task.call_args.args[0].close()


def test_cycle_thinking_level_none_when_not_supported(model_session):
    """模型不支持 thinking 时返回 None。"""
    model_session.agent.state.model = SimpleNamespace(reasoning=False)
    assert model_session._model.cycle_thinking_level() is None


# ---------------------------------------------------------------------------
# set_scoped_models
# ---------------------------------------------------------------------------


def test_set_scoped_models(model_session):
    """set_scoped_models 应覆盖 session.scoped_models。"""
    scoped = [SimpleNamespace(model=_make_model())]
    model_session.set_scoped_models(scoped)
    assert model_session.scoped_models is scoped
