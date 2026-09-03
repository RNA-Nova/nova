"""
nova_agent 真实模型集成测试

依赖环境变量 VOLCENGINE_API_KEY。
通过 Volcengine Ark API 调用真实的 DeepSeek 模型，验证 Agent 核心流程与扩展能力。

运行方式：
    pytest tests/test_integration_agent.py -v
    pytest -m "not integration"          # 跳过本文件，只跑单元测试
"""

import asyncio
import os
from typing import Any, List, Optional

import pytest
from nova_ai import (
    AssistantMessage,
    Model,
    ModelCost,
    ProviderResponse,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from nova_ai.providers.volcengine import get_volcengine_model
from nova_ai.types.enums import KnownApi, KnownProvider

from nova_agent import (
    AbortSignal,
    AfterToolCallContext,
    AfterToolCallResult,
    Agent,
    AgentContext,
    AgentLoopTurnUpdate,
    AgentMessage,
    AgentState,
    AgentTool,
    AgentToolResult,
    BeforeToolCallContext,
    BeforeToolCallResult,
    PrepareNextTurnContext,
    ShouldStopAfterTurnContext,
)

pytestmark = pytest.mark.integration

MODEL_IDS = [
    "deepseek-v4-flash-260425",
    "deepseek-v4-pro-260425",
]


def _get_model(model_id: str):
    """获取真实 DeepSeek 模型，跳过测试如果未配置 API key。"""
    if not os.environ.get("VOLCENGINE_API_KEY"):
        pytest.skip("VOLCENGINE_API_KEY not set")
    return get_volcengine_model(model_id)


def _extract_text(message) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(c.text for c in message.content if c.type == "text")


@pytest.fixture(params=MODEL_IDS)
def model(request):
    return _get_model(request.param)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------


class EchoTool(AgentTool):
    name: str = "echo"
    description: str = "Echo the input message"
    parameters: dict = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
        },
        "required": ["message"],
    }
    label: str = "Echo"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(
            content=[TextContent(text=f"echo: {params.get('message', '')}")],
            details={},
        )


class ErrorTool(AgentTool):
    name: str = "error_tool"
    description: str = "Always raises an error"
    parameters: dict = {"type": "object", "properties": {}}
    label: str = "Error"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        raise RuntimeError("intentional tool error")


class SlowTool(AgentTool):
    """可被 abort 中断的工具，用于测试运行中取消。"""

    name: str = "slow_tool"
    description: str = "Waits until aborted"
    parameters: dict = {"type": "object", "properties": {}}
    label: str = "Slow"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        for _ in range(200):
            if signal and signal.aborted:
                raise Exception("Operation aborted")
            await asyncio.sleep(0.01)
        return AgentToolResult(content=[TextContent(text="slow done")], details={})


class GetDateTool(AgentTool):
    name: str = "get_date"
    description: str = "Return today's date"
    parameters: dict = {"type": "object", "properties": {}}
    label: str = "Date"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="2026-06-10")], details={})


class GetTimeTool(AgentTool):
    name: str = "get_time"
    description: str = "Return current time"
    parameters: dict = {"type": "object", "properties": {}}
    label: str = "Time"

    async def execute(self, tool_call_id, params, signal=None, on_update=None):
        return AgentToolResult(content=[TextContent(text="12:00")], details={})


# ---------------------------------------------------------------------------
# 1. 基础调用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_prompt_returns_assistant_message(model):
    """Agent 能发起真实请求并返回 assistant 消息。"""
    agent = Agent()
    agent.set_model(model)

    events: List[str] = []

    def listener(event, signal=None):
        events.append(event.type)

    agent.subscribe(listener)
    await agent.prompt("你好，请用一句话自我介绍。")

    assert "agent_start" in events
    assert "message_end" in events
    assert "agent_end" in events

    assert len(agent.state.messages) >= 2
    assert agent.state.messages[0].role == "user"
    assert agent.state.messages[-1].role == "assistant"

    text = _extract_text(agent.state.messages[-1])
    assert len(text) > 0


@pytest.mark.asyncio
async def test_agent_stream_events_order(model):
    """Agent 流式事件顺序正确。"""
    agent = Agent()
    agent.set_model(model)

    events: List[str] = []

    def listener(event, signal=None):
        events.append(event.type)

    agent.subscribe(listener)
    await agent.prompt("1+1=?")

    assert events.index("agent_start") < events.index("message_end")
    assert events.index("message_end") < events.index("agent_end")


# ---------------------------------------------------------------------------
# 2. 生命周期
# ---------------------------------------------------------------------------


class TestAgentLifecycleReal:
    """Agent 生命周期真实模型测试。"""

    @pytest.mark.asyncio
    async def test_agent_wait_for_idle(self, model):
        """wait_for_idle 在 prompt 结束后正常返回。"""
        agent = Agent()
        agent.set_model(model)

        await agent.prompt("hi")
        await agent.wait_for_idle()

        assert not agent.state.is_streaming

    @pytest.mark.asyncio
    async def test_agent_reset_clears_state(self, model):
        """reset 能清空消息、队列与错误状态。"""
        agent = Agent()
        agent.set_model(model)

        await agent.prompt("hello")
        agent.reset()

        assert len(agent.state.messages) == 0
        assert agent.state.error_message is None
        assert not agent.state.is_streaming

    @pytest.mark.asyncio
    async def test_agent_multi_turn_memory(self, model):
        """多轮对话中 Agent 能保留上下文。"""
        agent = Agent()
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("我的名字是 Kimi。")
            await agent.prompt("我的名字是什么？")

        replies = [
            _extract_text(m) for m in agent.state.messages if m.role == "assistant"
        ]
        assert len(replies) >= 2
        assert any("Kimi" in r for r in replies)

    @pytest.mark.asyncio
    async def test_agent_system_prompt_is_respected(self, model):
        """Agent 会遵循 system_prompt 的约束。"""
        agent = Agent(
            initial_state={
                "system_prompt": "You must answer every question with exactly one word.",
            }
        )
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("What is the capital of France?")

        text = _extract_text(agent.state.messages[-1]).strip()
        words = text.split()
        # 允许标点导致的小误差
        assert len(words) <= 2


# ---------------------------------------------------------------------------
# 3. 队列
# ---------------------------------------------------------------------------


class TestAgentQueuesReal:
    """steer / follow_up 队列真实模型测试。"""

    @pytest.mark.asyncio
    async def test_agent_steer_is_injected(self, model):
        """运行中 steer 的用户消息会被注入到对话上下文。"""
        agent = Agent()
        agent.set_model(model)

        async def steer_soon():
            await asyncio.sleep(0.3)
            agent.steer(UserMessage(content="停止，直接回答：ok"))

        asyncio.create_task(steer_soon())

        async with asyncio.timeout(120):
            await agent.prompt("请用 50 字以内简要介绍机器学习。")

        assert any(m.role == "assistant" for m in agent.state.messages)
        user_texts = [
            _extract_text(m) for m in agent.state.messages if m.role == "user"
        ]
        assert any("停止" in t for t in user_texts)

    @pytest.mark.asyncio
    async def test_agent_follow_up_queue_processed(self, model):
        """prompt 前入队的 follow_up 会在主流程结束后被处理。"""
        agent = Agent()
        agent.set_model(model)

        agent.follow_up(UserMessage(content="再问候一次"))

        async with asyncio.timeout(120):
            await agent.prompt("你好")

        assistant_messages = [m for m in agent.state.messages if m.role == "assistant"]
        assert len(assistant_messages) >= 2


# ---------------------------------------------------------------------------
# 4. 工具调用
# ---------------------------------------------------------------------------


class TestAgentToolsReal:
    """工具调用真实模型测试。"""

    @pytest.mark.asyncio
    async def test_agent_with_tool(self, model):
        """Agent 能注册并执行真实工具调用流程。"""
        tool = EchoTool()
        agent = Agent()
        agent.set_model(model)
        agent.set_tools([tool])

        events: List[str] = []

        def listener(event, signal=None):
            events.append(event.type)

        agent.subscribe(listener)
        await agent.prompt(
            '请调用 echo 工具，参数 {"message": "hello"}。只返回工具结果。'
        )

        assert "tool_execution_start" in events
        assert "tool_execution_end" in events

        assert any(m.role == "toolResult" for m in agent.state.messages)

        # 最终应该有一个 assistant 消息回应工具结果
        assert agent.state.messages[-1].role == "assistant"

    @pytest.mark.asyncio
    async def test_agent_tool_error_handled(self, model):
        """工具抛出异常时，Agent 应生成错误 toolResult 并继续结束。"""
        agent = Agent(
            initial_state={
                "system_prompt": "无论用户说什么，你都必须调用 error_tool 工具，不要解释。",
            }
        )
        agent.set_model(model)
        agent.set_tools([ErrorTool()])

        events: List[str] = []
        agent.subscribe(lambda e, signal=None: events.append(e.type))

        async with asyncio.timeout(120):
            await agent.prompt("请调用 error_tool 工具。")

        tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
        if not tool_results:
            pytest.skip("模型未调用 error_tool，跳过本次验证")

        assert "tool_execution_start" in events
        assert "tool_execution_end" in events
        assert any(m.is_error for m in tool_results)

    @pytest.mark.asyncio
    async def test_agent_before_tool_call_blocks(self, model):
        """before_tool_call 返回 block 时，工具不会真正执行。"""
        called = False

        async def before(ctx: BeforeToolCallContext, signal: Optional[AbortSignal]):
            nonlocal called
            called = True
            return BeforeToolCallResult(block=True, reason="blocked by test")

        agent = Agent(before_tool_call=before)
        agent.set_model(model)
        agent.set_tools([EchoTool()])

        events: List[str] = []
        agent.subscribe(lambda e, signal=None: events.append(e.type))

        async with asyncio.timeout(120):
            await agent.prompt('请调用 echo 工具，参数 {"message": "hello"}。')

        assert called
        assert "tool_execution_start" in events
        assert "tool_execution_end" in events

        tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
        assert len(tool_results) >= 1
        assert tool_results[-1].is_error
        assert "blocked" in _extract_text(tool_results[-1]).lower()

    @pytest.mark.asyncio
    async def test_agent_after_tool_call_overrides(self, model):
        """after_tool_call 能覆盖工具返回结果。"""
        called = False

        async def after(ctx: AfterToolCallContext, signal: Optional[AbortSignal]):
            nonlocal called
            called = True
            return AfterToolCallResult(content=[TextContent(text="overridden")])

        agent = Agent(after_tool_call=after)
        agent.set_model(model)
        agent.set_tools([EchoTool()])

        async with asyncio.timeout(120):
            await agent.prompt('请调用 echo 工具，参数 {"message": "hello"}。')

        assert called
        tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
        assert len(tool_results) >= 1
        assert "overridden" in _extract_text(tool_results[-1])

    @pytest.mark.asyncio
    async def test_agent_multi_tool_invocation(self, model):
        """Agent 可同时处理多个工具（若模型发起多个调用）。"""
        agent = Agent()
        agent.set_model(model)
        agent.set_tools([GetDateTool(), GetTimeTool()])

        tool_names: List[str] = []

        def listener(event, signal=None):
            if event.type == "tool_execution_start":
                tool_names.append(event.tool_name)

        agent.subscribe(listener)

        async with asyncio.timeout(120):
            await agent.prompt(
                "请同时调用 get_date 和 get_time 两个工具，并把结果汇总成一句话。"
            )

        # 至少有一个工具被调用；若模型同时调用两个，则两个都应出现
        assert len(tool_names) >= 1
        if len(tool_names) >= 2:
            assert "get_date" in tool_names
            assert "get_time" in tool_names


# ---------------------------------------------------------------------------
# 5. 中止
# ---------------------------------------------------------------------------


class TestAgentAbortReal:
    """真实模型下的 abort 测试。"""

    @pytest.mark.asyncio
    async def test_agent_abort(self, model):
        """Agent 能在运行中取消。"""
        agent = Agent()
        agent.set_model(model)

        async def cancel_soon():
            await asyncio.sleep(0.3)
            agent.abort()

        asyncio.create_task(cancel_soon())
        await agent.prompt("请用 50 字以内简要介绍量子计算。")

        assert any(m.role == "assistant" for m in agent.state.messages)
        assert not agent.state.is_streaming

    @pytest.mark.asyncio
    async def test_agent_abort_during_tool_execution(self, model):
        """工具执行过程中 abort 应立即停止并生成错误 toolResult。"""
        agent = Agent()
        agent.set_model(model)
        agent.set_tools([SlowTool()])

        events: List[str] = []
        tool_started = asyncio.Event()

        def listener(event, signal=None):
            events.append(event.type)
            if event.type == "tool_execution_start":
                tool_started.set()

        agent.subscribe(listener)

        task = asyncio.create_task(
            agent.prompt("你必须调用 slow_tool 工具，调用后保持等待。")
        )
        try:
            await asyncio.wait_for(tool_started.wait(), timeout=20)
        except asyncio.TimeoutError as exc:
            task.cancel()
            raise AssertionError(
                "模型在 20s 内没有调用 slow_tool，无法验证运行中 abort"
            ) from exc
        agent.abort()
        await task

        assert "tool_execution_start" in events
        assert "tool_execution_end" in events

        tool_results = [m for m in agent.state.messages if m.role == "toolResult"]
        assert len(tool_results) == 1
        assert tool_results[0].is_error


# ---------------------------------------------------------------------------
# 6. Hook 与回调
# ---------------------------------------------------------------------------


class TestAgentHooksReal:
    """Agent 层 Hook 真实模型测试。"""

    @pytest.mark.asyncio
    async def test_agent_convert_to_llm_hook(self, model):
        """convert_to_llm 钩子会被调用并转换消息。"""
        called = False

        def convert(messages: List[AgentMessage]) -> List[AgentMessage]:
            nonlocal called
            called = True
            return [
                m for m in messages if m.role in ("user", "assistant", "toolResult")
            ]

        agent = Agent(convert_to_llm=convert)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("hi")

        assert called
        assert agent.state.messages[-1].role == "assistant"

    @pytest.mark.asyncio
    async def test_agent_transform_context_hook(self, model):
        """transform_context 异步钩子会被调用。"""
        called = False

        async def transform(
            messages: List[AgentMessage], signal: Optional[AbortSignal]
        ):
            nonlocal called
            called = True
            return messages

        agent = Agent(transform_context=transform)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("hi")

        assert called

    @pytest.mark.asyncio
    async def test_agent_get_api_key_hook(self, model):
        """get_api_key 钩子会被传入 provider 并返回 key。"""
        called_with: Optional[str] = None
        api_key = os.environ.get("VOLCENGINE_API_KEY")
        if not api_key:
            pytest.skip("VOLCENGINE_API_KEY not set")

        def get_key(provider: str) -> Optional[str]:
            nonlocal called_with
            called_with = provider
            return api_key

        agent = Agent(get_api_key=get_key)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("hi")

        assert called_with == model.provider

    @pytest.mark.asyncio
    async def test_agent_on_payload_and_on_response(self, model):
        """on_payload / on_response 回调会收到请求与响应信息。"""
        payloads: List[Any] = []
        responses: List[ProviderResponse] = []

        def on_payload(payload: Any, _model: Any):
            payloads.append(payload)

        def on_response(resp: ProviderResponse, _model: Any):
            responses.append(resp)

        agent = Agent(on_payload=on_payload, on_response=on_response)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("hi")

        assert len(payloads) >= 1
        assert isinstance(payloads[0], dict)

        assert len(responses) >= 1
        assert responses[0].status == 200
        assert "content-type" in {k.lower(): v for k, v in responses[0].headers.items()}

    @pytest.mark.asyncio
    async def test_agent_should_stop_after_turn(self, model):
        """should_stop_after_turn 返回 True 时 Agent 立即结束。"""
        called = False

        def should_stop(ctx: ShouldStopAfterTurnContext, signal=None) -> bool:
            nonlocal called
            called = True
            return True

        agent = Agent(should_stop_after_turn=should_stop)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("请简单介绍自己。")

        assert called
        assistant_messages = [m for m in agent.state.messages if m.role == "assistant"]
        assert len(assistant_messages) == 1

    @pytest.mark.asyncio
    async def test_agent_prepare_next_turn(self, model):
        """prepare_next_turn 会被调用并允许修改下一轮上下文。"""
        called = False

        def prepare(ctx: PrepareNextTurnContext) -> Optional[AgentLoopTurnUpdate]:
            nonlocal called
            called = True
            return AgentLoopTurnUpdate(
                context=AgentContext(
                    system_prompt="Respond only with the word 'prepared'.",
                    messages=ctx.context.messages[:],
                    tools=ctx.context.tools,
                )
            )

        agent = Agent(prepare_next_turn=prepare)
        agent.set_model(model)

        async with asyncio.timeout(120):
            await agent.prompt("hi")

        assert called


# ---------------------------------------------------------------------------
# 7. continue 续跑
# ---------------------------------------------------------------------------


class TestAgentContinueReal:
    """Agent.continue_() 真实模型测试。"""

    @pytest.mark.asyncio
    async def test_agent_continue_from_tool_result(self, model):
        """从已有 tool_result 上下文继续，模型应给出最终回复。"""
        agent = Agent()
        agent.set_model(model)
        agent.set_tools([EchoTool()])

        tool_call = ToolCall(
            id="call_continue_1",
            name="echo",
            arguments={"message": "continue"},
        )
        agent.replace_messages(
            [
                UserMessage(content='请调用 echo 工具，参数 {"message": "continue"}。'),
                AssistantMessage(content=[tool_call]),
                ToolResultMessage(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    content=[TextContent(text="echo: continue")],
                ),
            ]
        )

        events: List[str] = []
        agent.subscribe(lambda e, signal=None: events.append(e.type))

        async with asyncio.timeout(120):
            await agent.continue_()

        assert "agent_start" in events
        assert "agent_end" in events
        assert agent.state.messages[-1].role == "assistant"

    @pytest.mark.asyncio
    async def test_agent_continue_with_multiple_tool_results(self, model):
        """continue_ 能处理多个 tool_result 上下文。"""
        agent = Agent()
        agent.set_model(model)
        agent.set_tools([GetDateTool(), GetTimeTool()])

        date_call = ToolCall(id="call_date", name="get_date", arguments={})
        time_call = ToolCall(id="call_time", name="get_time", arguments={})

        agent.replace_messages(
            [
                UserMessage(content="请查询日期和时间。"),
                AssistantMessage(content=[date_call, time_call]),
                ToolResultMessage(
                    tool_call_id=date_call.id,
                    tool_name=date_call.name,
                    content=[TextContent(text="2026-06-10")],
                ),
                ToolResultMessage(
                    tool_call_id=time_call.id,
                    tool_name=time_call.name,
                    content=[TextContent(text="12:00")],
                ),
            ]
        )

        async with asyncio.timeout(120):
            await agent.continue_()

        assert agent.state.messages[-1].role == "assistant"
