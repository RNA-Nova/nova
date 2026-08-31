"""ModelController 思考级别行为测试（对齐 TS ``setThinkingLevel`` 语义）。"""

from unittest.mock import MagicMock

import pytest
from nova_ai import Model, ModelCost, ModelThinkingLevel

from nova_harness.core.agent_session.controllers import ModelController


def _model(reasoning: bool = True, thinking_level_map=None) -> Model:
    return Model(
        id="m",
        name="m",
        api="openai",
        provider="openai",
        base_url="",
        reasoning=reasoning,
        input_types=["text"],
        cost=ModelCost(input=0, output=0),
        context_window=128000,
        max_tokens=4096,
        thinking_level_map=thinking_level_map,
    )


def _session(model: Model, thinking_level: ModelThinkingLevel) -> MagicMock:
    session = MagicMock()
    session.model = model
    session.thinking_level = thinking_level
    session._extension_runner = None
    return session


@pytest.mark.asyncio
async def test_set_thinking_level_clamps_to_model_support():
    """请求的级别不受模型支持时，状态保存吸附后的级别。"""
    session = _session(
        _model(thinking_level_map={"high": None}),
        ModelThinkingLevel.LOW,
    )
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.HIGH)

    # high 被禁用：向上无更高级，向下吸附到 medium
    session.agent.set_thinking_level.assert_called_once_with(ModelThinkingLevel.MEDIUM)
    session.session_manager.append_thinking_level_change.assert_called_once_with(
        ModelThinkingLevel.MEDIUM
    )


@pytest.mark.asyncio
async def test_set_thinking_level_off_not_persisted_for_non_reasoning_model():
    """非推理模型被吸附为 OFF 时不写全局默认，避免污染用户偏好。"""
    session = _session(_model(reasoning=False), ModelThinkingLevel.MEDIUM)
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.HIGH)

    session.agent.set_thinking_level.assert_called_once_with(ModelThinkingLevel.OFF)
    session.settings_manager.set_default_thinking_level.assert_not_called()


@pytest.mark.asyncio
async def test_set_thinking_level_off_persisted_for_reasoning_model():
    """推理模型上用户主动关思考时，OFF 写入全局默认。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.OFF)

    session.agent.set_thinking_level.assert_called_once_with(ModelThinkingLevel.OFF)
    session.settings_manager.set_default_thinking_level.assert_called_once_with(
        ModelThinkingLevel.OFF
    )


@pytest.mark.asyncio
async def test_set_thinking_level_noop_when_unchanged():
    """级别未变化时不写会话条目、不改状态。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.MEDIUM)

    session.agent.set_thinking_level.assert_not_called()
    session.session_manager.append_thinking_level_change.assert_not_called()


def test_get_available_thinking_levels_without_model_excludes_xhigh():
    """无模型时对齐 TS：不含 xhigh（xhigh 需模型显式声明支持）。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    session.model = None
    controller = ModelController(session)

    levels = controller.get_available_thinking_levels()

    assert ModelThinkingLevel.OFF in levels
    assert ModelThinkingLevel.HIGH in levels
    assert ModelThinkingLevel.XHIGH not in levels


def test_get_available_thinking_levels_without_model_excludes_max():
    """无模型时对齐 TS：不含 max（max 需模型显式声明支持）。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    session.model = None
    controller = ModelController(session)

    levels = controller.get_available_thinking_levels()

    assert ModelThinkingLevel.OFF in levels
    assert ModelThinkingLevel.HIGH in levels
    assert ModelThinkingLevel.MAX not in levels


@pytest.mark.asyncio
async def test_set_thinking_level_xhigh_clamped_to_off_without_model():
    """无模型时请求 XHIGH 吸附为 OFF（xhigh 需模型显式声明支持，对齐 TS）。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    session.model = None
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.XHIGH)

    session.agent.set_thinking_level.assert_called_once_with(ModelThinkingLevel.OFF)


@pytest.mark.asyncio
async def test_set_thinking_level_max_clamped_to_off_without_model():
    """无模型时请求 MAX 吸附为 OFF（max 需模型显式声明支持，对齐 TS）。"""
    session = _session(_model(), ModelThinkingLevel.MEDIUM)
    session.model = None
    controller = ModelController(session)

    await controller.set_thinking_level(ModelThinkingLevel.MAX)

    session.agent.set_thinking_level.assert_called_once_with(ModelThinkingLevel.OFF)


def test_model_switch_falls_back_to_default_constant_when_no_user_default():
    """切模型时用户从未设默认级别：回退到 DEFAULT_THINKING_LEVEL（MEDIUM，对齐 TS）。"""
    session = _session(_model(reasoning=False), ModelThinkingLevel.OFF)
    session.settings_manager.get_default_thinking_level.return_value = None
    controller = ModelController(session)

    assert (
        controller._get_thinking_level_for_model_switch() == ModelThinkingLevel.MEDIUM
    )
