"""Subagent 扩展注册与工具执行单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from subagent.extension import _build_calls, _execute_subagent, _validate_mode
from subagent.types import SubagentCall


def test_validate_mode_single():
    assert _validate_mode({"agent": "scout", "task": "find"}) == "single"


def test_validate_mode_parallel():
    assert _validate_mode({"tasks": [{"agent": "scout", "task": "find"}]}) == "parallel"


def test_validate_mode_chain():
    assert _validate_mode({"chain": [{"agent": "scout", "task": "find"}]}) == "chain"


def test_validate_mode_ambiguous_fails():
    with pytest.raises(ValueError, match="exactly one mode"):
        _validate_mode({"agent": "scout", "tasks": []})


def test_validate_mode_too_many_parallel():
    with pytest.raises(ValueError, match="Too many parallel tasks"):
        _validate_mode({"tasks": [{"agent": "scout", "task": "t"} for _ in range(9)]})


def test_build_calls_single():
    calls = _build_calls({"agent": "scout", "task": "find", "cwd": "/tmp"}, "single")
    assert calls == [SubagentCall(agent="scout", task="find", cwd="/tmp")]


def test_build_calls_parallel():
    calls = _build_calls(
        {"tasks": [{"agent": "a", "task": "t1"}, {"agent": "b", "task": "t2"}]},
        "parallel",
    )
    assert len(calls) == 2
    assert calls[0].agent == "a"
    assert calls[1].agent == "b"


def test_build_calls_chain():
    calls = _build_calls(
        {"chain": [{"agent": "a", "task": "t1"}]},
        "chain",
    )
    assert calls == [SubagentCall(agent="a", task="t1", cwd=None)]


@pytest.mark.asyncio
async def test_execute_subagent_single_success():
    """single 模式成功返回子 agent 输出。"""
    runtime = AsyncMock()
    runtime.prompt = AsyncMock()
    runtime.agent.state.messages = [
        SimpleNamespace(
            role="assistant",
            content=[SimpleNamespace(type="text", text="scout output")],
            usage=None,
            stop_reason="end",
        )
    ]

    ctx = MagicMock()
    ctx.create_subagent_session = AsyncMock(return_value=runtime)

    result = await _execute_subagent(
        ctx, "tc1", {"agent": "scout", "task": "find auth"}
    )

    assert result.content[0].text == "scout output"
    assert result.details["agent"] == "scout"
    ctx.create_subagent_session.assert_awaited_once_with("scout", None)


@pytest.mark.asyncio
async def test_execute_subagent_single_error():
    """single 模式失败时 is_error=True。"""
    runtime = AsyncMock()
    runtime.prompt = AsyncMock(side_effect=RuntimeError("subagent crashed"))

    ctx = MagicMock()
    ctx.create_subagent_session = AsyncMock(return_value=runtime)

    result = await _execute_subagent(
        ctx, "tc1", {"agent": "scout", "task": "find auth"}
    )

    assert "subagent crashed" in result.content[0].text


@pytest.mark.asyncio
async def test_execute_subagent_chain_success():
    """chain 模式顺序执行并替换 {previous}。"""
    created = {}

    async def make_runtime(name, cwd=None):
        runtime = AsyncMock()
        runtime.prompt = AsyncMock()
        if name == "scout":
            output = "scout findings"
        else:
            output = "planner plan"
        runtime.agent.state.messages = [
            SimpleNamespace(
                role="assistant",
                content=[SimpleNamespace(type="text", text=output)],
                usage=None,
                stop_reason="end",
            )
        ]
        created[name] = runtime
        return runtime

    ctx = MagicMock()
    ctx.create_subagent_session = AsyncMock(side_effect=make_runtime)

    result = await _execute_subagent(
        ctx,
        "tc1",
        {
            "chain": [
                {"agent": "scout", "task": "find"},
                {"agent": "planner", "task": "plan {previous}"},
            ]
        },
    )

    assert result.content[0].text == "planner plan"
    calls = ctx.create_subagent_session.await_args_list
    assert calls[0].args == ("scout", None)
    assert calls[1].args == ("planner", None)
    created["planner"].prompt.assert_awaited_once_with("plan scout findings")


@pytest.mark.asyncio
async def test_execute_subagent_parallel_success():
    """parallel 模式返回汇总输出。"""

    async def make_runtime(name, cwd=None):
        runtime = AsyncMock()
        runtime.prompt = AsyncMock()
        runtime.agent.state.messages = [
            SimpleNamespace(
                role="assistant",
                content=[SimpleNamespace(type="text", text=f"{name} done")],
                usage=None,
                stop_reason="end",
            )
        ]
        return runtime

    ctx = MagicMock()
    ctx.create_subagent_session = AsyncMock(side_effect=make_runtime)

    result = await _execute_subagent(
        ctx,
        "tc1",
        {
            "tasks": [
                {"agent": "scout", "task": "find models"},
                {"agent": "scout", "task": "find providers"},
            ]
        },
    )

    text = result.content[0].text
    assert "Parallel: 2/2 succeeded" in text
    assert "[scout] completed" in text
