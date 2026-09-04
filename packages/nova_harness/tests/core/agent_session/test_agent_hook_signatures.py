"""Agent 钩子签名契约的回归测试。

vendored nova_agent 的 ``invoke_hook`` 对全部 turn 钩子统一传
``(ctx, signal)`` 两个位置参数——harness 侧安装的钩子必须同形。
``should_stop_after_turn`` 曾缺 ``signal`` 形参，真实对话每轮结束炸
TypeError，而 PTY 冒烟的标记断言捕获不到 turn 完成后的崩溃。
"""

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nova_harness.core.agent_session.agent import AgentSession
from nova_harness.core.types.session.config import AgentSessionConfig

_TURN_HOOKS = [
    "before_tool_call",
    "after_tool_call",
    "prepare_next_turn",
    "should_stop_after_turn",
    "transform_context",
]


def _make_session() -> AgentSession:
    """最小可用 AgentSession（MagicMock 依赖 + MagicMock agent）。"""
    agent = MagicMock()
    agent.state.messages = []
    agent.state.is_streaming = False
    config = AgentSessionConfig(
        agent=agent,
        session_manager=MagicMock(),
        settings_manager=MagicMock(),
        cwd="/tmp",
        system_prompt_manager=MagicMock(),
        tools_manager=MagicMock(),
        resource_loader=MagicMock(),
        model_runtime=MagicMock(),
        scoped_models=[],
        initial_active_tool_names=[],
        base_tools_override=None,
        extension_runner_ref=None,
        session_start_event=None,
    )
    return AgentSession(config)


def test_agent_hooks_accept_ctx_and_signal():
    """全部 turn 钩子签名同形 (ctx, signal=None)——与 invoke_hook 的
    统一两参调用对齐，签名漂移在本测试响亮失败。"""
    session = _make_session()
    for name in _TURN_HOOKS:
        hook = getattr(session.agent, name)
        params = list(inspect.signature(hook).parameters)
        assert len(params) == 2, f"{name} 签名漂移：{params}"


@pytest.mark.asyncio
async def test_should_stop_after_turn_two_args_no_runner():
    """两参调用不炸；无扩展 runner 时返回 False（不停止）。"""
    session = _make_session()
    session._extension_runner = None
    ctx = SimpleNamespace(turn_index=0, message=None, tool_results=[])
    result = await session.agent.should_stop_after_turn(ctx, None)
    assert result is False
