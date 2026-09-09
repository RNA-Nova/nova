"""ToolExecutionGate 单元测试 + 门控并行的循环集成测试。

矩阵（codex parallel.rs 公平 RwLock 语义的 asyncio 移植验证）：
- 门单元：读者重叠 / 写者独占 / FIFO 公平 / 等锁取消自摘 / 授予后取消撤销；
- 集成：混合批 parallel 重叠 + sequential 独占、双 sequential FIFO、
  等门期间 abort 不起跑、完成乱序但结果按提交序。
"""

import asyncio
from typing import Any, List

import pytest
from helpers import EchoTool, SlowTool, multi_tool_call_stream, text_stream
from nova_agent import Agent, AgentToolResult
from nova_agent.agent_loop.execution_gate import ToolExecutionGate
from nova_ai import TextContent, UserMessage

# ---------------------------------------------------------------------------
# 门单元测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_readers_overlap() -> None:
    gate = ToolExecutionGate()
    await gate.acquire(False)
    await gate.acquire(False)  # 第二个读者立即进入
    await gate.release(False)
    await gate.release(False)


@pytest.mark.asyncio
async def test_gate_writer_waits_for_readers() -> None:
    gate = ToolExecutionGate()
    events: List[str] = []
    await gate.acquire(False)

    async def writer() -> None:
        await gate.acquire(True)
        events.append("writer-in")
        await gate.release(True)

    task = asyncio.create_task(writer())
    await asyncio.sleep(0.05)
    assert events == []  # 写者在等读者排空
    await gate.release(False)
    await task
    assert events == ["writer-in"]


@pytest.mark.asyncio
async def test_gate_fifo_writer_blocks_later_readers() -> None:
    """写者排队后新读者不插队（写者优先防饿死，FIFO 公平）。"""
    gate = ToolExecutionGate()
    order: List[str] = []
    await gate.acquire(False)  # r1 持有

    async def reader(name: str) -> None:
        await gate.acquire(False)
        order.append(name)
        await asyncio.sleep(0)
        await gate.release(False)

    async def writer() -> None:
        await gate.acquire(True)
        order.append("w1")
        await gate.release(True)

    w = asyncio.create_task(writer())  # 先排队
    await asyncio.sleep(0)
    r2 = asyncio.create_task(reader("r2"))  # 后到——应排在写者后
    await gate.release(False)  # r1 放行
    await asyncio.gather(w, r2)
    assert order == ["w1", "r2"]


@pytest.mark.asyncio
async def test_gate_cancelled_waiter_self_removes_and_never_runs() -> None:
    gate = ToolExecutionGate()
    await gate.acquire(True)  # 写者持有

    waiter = asyncio.create_task(gate.acquire(False))  # 读者排队
    await asyncio.sleep(0.05)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await gate.release(True)
    # 队列已自摘：新写者应能立即进入（若残留假等待者会卡死）
    await asyncio.wait_for(gate.acquire(True), timeout=1)
    await gate.release(True)


# ---------------------------------------------------------------------------
# 循环集成测试（混合批门控）
# ---------------------------------------------------------------------------


class _TimelineTool(EchoTool):
    """记录每次执行的 [start, end) 区间（loop.time）。"""

    timeline: List[tuple] = []

    def __init__(self, delay: float = 0.05, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._delay = delay

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(self._delay)
        end = loop.time()
        type(self).timeline.append((params["message"], start, end))
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params['message']}")],
            details={},
        )


class _TimelineSlowTool(SlowTool):
    """sequential 声明 + 区间记录。"""

    timeline: List[tuple] = []

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        loop = asyncio.get_running_loop()
        start = loop.time()
        await asyncio.sleep(0.05)
        end = loop.time()
        type(self).timeline.append((params["value"], start, end))
        return AgentToolResult(
            content=[TextContent(text=f"slow-{params['value']}")],
            details={},
        )


def _overlap(a: tuple, b: tuple) -> bool:
    return a[1] < b[2] and b[1] < a[2]


@pytest.mark.asyncio
async def test_mixed_batch_parallel_overlap_sequential_exclusive(dummy_model) -> None:
    """[echo, echo, slow(sequential)]：两个 echo 重叠执行，slow 独占且不与任何人重叠。"""
    _TimelineTool.timeline = []
    _TimelineSlowTool.timeline = []

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return multi_tool_call_stream(
                model,
                [
                    ("echo", {"message": "p1"}),
                    ("echo", {"message": "p2"}),
                    ("slow", {"value": "s1"}),
                ],
            )
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([_TimelineTool(), _TimelineSlowTool()])

    await agent.prompt(UserMessage(role="user", content="go"))

    fast = _TimelineTool.timeline
    slow = _TimelineSlowTool.timeline
    assert len(fast) == 2 and len(slow) == 1
    assert _overlap(fast[0], fast[1]), "parallel 工具应重叠执行"
    assert not _overlap(slow[0], fast[0]) and not _overlap(
        slow[0], fast[1]
    ), "sequential 工具不得与任何 parallel 工具重叠"
    # 结果按提交序回写
    results = [m for m in agent.state.messages if m.role == "toolResult"]
    assert [r.content[0].text for r in results] == ["echo: p1", "echo: p2", "slow-s1"]


@pytest.mark.asyncio
async def test_two_sequential_tools_fifo_order(dummy_model) -> None:
    """两个 sequential 工具按提交 FIFO 顺序独占执行。"""
    _TimelineSlowTool.timeline = []

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            return multi_tool_call_stream(
                model,
                [("slow", {"value": "first"}), ("slow", {"value": "second"})],
            )
        return text_stream(model, "ok")

    agent = Agent(stream_fn=stream_fn, tool_execution="parallel")
    agent.set_model(dummy_model)
    agent.set_tools([_TimelineSlowTool()])

    await agent.prompt(UserMessage(role="user", content="go"))

    timeline = _TimelineSlowTool.timeline
    assert [entry[0] for entry in timeline] == ["first", "second"]
    assert not _overlap(timeline[0], timeline[1])


@pytest.mark.asyncio
async def test_abort_while_waiting_on_gate_never_executes(dummy_model) -> None:
    """sequential 等门期间 abort：永不起跑，结果含 aborted 且批正常收尾。"""
    from nova_agent import AgentContext, AgentLoopConfig
    from nova_agent.agent_loop import run_agent_loop
    from nova_ai import AbortController, SimpleStreamOptions

    ran: List[str] = []

    class GateSlowTool(SlowTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            ran.append(params["value"])
            return AgentToolResult(content=[TextContent(text="x")], details={})

    class BlockingEchoTool(EchoTool):
        async def execute(self, tool_call_id, params, signal=None, on_update=None):
            # 占住读门直到 abort 到达
            assert signal is not None
            await signal.wait()
            return AgentToolResult(
                content=[TextContent(text="echo-aborted")], details={}
            )

    controller = AbortController()

    async def emit(event: Any) -> None:
        pass

    step = 0

    async def stream_fn(model, context, options):
        nonlocal step
        step += 1
        if step == 1:
            # 批次：[blocking echo（读门）, slow（写门等待）]
            return multi_tool_call_stream(
                model,
                [("echo", {"message": "block"}), ("slow", {"value": "gated"})],
            )
        from helpers import final_stream

        return final_stream(model, "aborted", stop_reason="aborted")

    async def abort_later() -> None:
        await asyncio.sleep(0.1)
        controller.abort()

    asyncio.create_task(abort_later())
    await run_agent_loop(
        [UserMessage(role="user", content="go")],
        AgentContext(
            system_prompt="sys", messages=[], tools=[BlockingEchoTool(), GateSlowTool()]
        ),
        AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model),
        emit,
        signal=controller.signal,
        stream_fn=stream_fn,
    )

    assert ran == [], "等门期间被 abort 的 sequential 工具不得起跑"
