"""Subagent runner 单元测试。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from subagent.runner import (
    _truncate_output,
    format_parallel_output,
    run_subagent_chain,
    run_subagent_parallel,
    run_subagent_single,
)
from subagent.types import SubagentCall, SubagentResult, SubagentUsage


def _make_runtime(output: str, error: str | None = None):
    """构造一个模拟的子 agent runtime。"""
    runtime = AsyncMock()
    runtime.prompt = AsyncMock()

    if error:
        runtime.prompt.side_effect = RuntimeError(error)
    else:
        msg = SimpleNamespace(
            role="assistant",
            content=[SimpleNamespace(type="text", text=output)],
            usage=None,
            stop_reason="end",
        )
        runtime.agent.state.messages = [msg]

    return runtime


@pytest.mark.asyncio
async def test_run_subagent_single_returns_output():
    """single 模式返回子 agent 最终输出。"""
    runtime = _make_runtime("found auth code")

    async def create_session(name, cwd=None):
        return runtime

    result = await run_subagent_single(
        SubagentCall(agent="scout", task="find auth"), create_session
    )

    assert result.agent == "scout"
    assert result.task == "find auth"
    assert result.output == "found auth code"
    assert result.error is None
    runtime.prompt.assert_awaited_once_with("find auth")


@pytest.mark.asyncio
async def test_run_subagent_single_captures_error():
    """子 agent 执行异常时结果携带 error。"""
    runtime = _make_runtime("", error="boom")

    async def create_session(name, cwd=None):
        return runtime

    result = await run_subagent_single(
        SubagentCall(agent="scout", task="find auth"), create_session
    )

    assert result.error == "boom"


@pytest.mark.asyncio
async def test_run_subagent_parallel_limited_concurrency():
    """parallel 模式限制并发并返回所有结果。"""
    created = []

    async def create_session(name, cwd=None):
        runtime = _make_runtime(f"output from {name}")
        created.append(name)
        return runtime

    calls = [
        SubagentCall(agent="scout", task="task1"),
        SubagentCall(agent="scout", task="task2"),
        SubagentCall(agent="scout", task="task3"),
    ]

    results = await run_subagent_parallel(calls, create_session)

    assert len(results) == 3
    assert {r.output for r in results} == {
        "output from scout",
    }


@pytest.mark.asyncio
async def test_run_subagent_parallel_too_many_tasks():
    """parallel 任务数超过上限时抛出异常。"""
    calls = [SubagentCall(agent="scout", task=f"task{i}") for i in range(9)]

    async def create_session(name, cwd=None):
        return _make_runtime("")

    with pytest.raises(ValueError, match="Too many parallel tasks"):
        await run_subagent_parallel(calls, create_session)


@pytest.mark.asyncio
async def test_run_subagent_chain_replaces_previous():
    """chain 模式正确替换 {previous} 占位符。"""
    created = []

    created: dict = {}

    async def create_session(name, cwd=None):
        runtime = _make_runtime(f"{name} result")
        created[name] = runtime
        return runtime

    calls = [
        SubagentCall(agent="scout", task="find auth"),
        SubagentCall(agent="planner", task="plan using: {previous}"),
    ]

    results = await run_subagent_chain(calls, create_session)

    assert len(results) == 2
    assert results[0].output == "scout result"
    assert results[1].output == "planner result"
    created["planner"].prompt.assert_awaited_once_with("plan using: scout result")


@pytest.mark.asyncio
async def test_run_subagent_chain_stops_on_error():
    """chain 模式遇到失败时停止后续步骤。"""

    async def create_session(name, cwd=None):
        if name == "scout":
            return _make_runtime("scout result")
        return _make_runtime("", error="failed")

    calls = [
        SubagentCall(agent="scout", task="step1"),
        SubagentCall(agent="planner", task="step2"),
        SubagentCall(agent="worker", task="step3"),
    ]

    results = await run_subagent_chain(calls, create_session)

    assert len(results) == 2
    assert results[1].error == "failed"


def test_truncate_output_under_cap():
    """短输出不被截断。"""
    assert _truncate_output("hello") == "hello"


def test_truncate_output_long():
    """长输出按字节截断并附加提示。"""
    long_text = "x" * 100 * 1024
    truncated = _truncate_output(long_text, cap=50 * 1024)
    assert "[Output truncated" in truncated
    assert len(truncated.encode("utf-8")) <= 50 * 1024 + 200


def test_format_parallel_output():
    """并行结果格式化包含每个 agent 状态。"""
    results = [
        SubagentResult(agent="a", task="t1", output="ok", usage=SubagentUsage()),
        SubagentResult(agent="b", task="t2", error="fail", usage=SubagentUsage()),
    ]
    text = format_parallel_output(results)
    assert "Parallel: 1/2 succeeded" in text
    assert "[a] completed" in text
    assert "[b] failed" in text
