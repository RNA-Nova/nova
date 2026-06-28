"""
nova_agent 基础测试
验证 Pydantic 迁移后核心类型可创建、可序列化，Agent 可初始化。
"""

import pytest
from nova_agent import (
    Agent,
    AgentContext,
    AgentState,
    AgentLoopConfig,
    AgentTool,
    AgentToolResult,
    AgentStartEvent,
    AgentEndEvent,
    CustomAgentMessage,
    AbortSignal,
)
from nova_ai import TextContent, ImageContent, Model, ModelCost, KnownApi, KnownProvider


def test_abort_signal():
    signal = AbortSignal()
    assert not signal.aborted
    assert not signal.is_set()
    signal.set()
    assert signal.aborted
    assert signal.is_set()
    signal.clear()
    assert not signal.aborted


def test_custom_agent_message():
    class MyMessage(CustomAgentMessage):
        value: int = 0

    msg = MyMessage(value=42)
    assert msg.value == 42


def test_agent_context():
    ctx = AgentContext(system_prompt="test", messages=[])
    assert ctx.system_prompt == "test"
    data = ctx.model_dump()
    assert data["system_prompt"] == "test"


def test_agent_state():
    state = AgentState()
    assert state.messages == []
    assert state.pending_tool_calls == set()
    assert state.is_streaming is False


def test_agent_tool_result():
    result = AgentToolResult(content=[TextContent(text="hello")], details={"k": "v"})
    assert result.content[0].text == "hello"
    data = result.model_dump()
    assert data["content"][0]["text"] == "hello"


def test_agent_loop_config():
    model = Model(
        id="deepseek-v3-2-251201",
        name="DeepSeek",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.VOLCENGINE,
        base_url="https://ark.cn-beijing.volces.com/api/v3/",
        max_tokens=4096,
        context_window=131072,
        input_types=["text"],
        reasoning=False,
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
    )
    config = AgentLoopConfig(
        model=model,
        temperature=0.5,
        max_tokens=100,
    )
    assert config.model == model
    assert config.temperature == 0.5
    assert config.max_tokens == 100
    # Callback fields should be excluded from serialization
    data = config.model_dump()
    assert "convert_to_llm" not in data
    assert "model" in data


def test_agent_init():
    agent = Agent()
    assert agent.state is not None
    assert agent.state.model is not None
    assert agent.state.messages == []


def test_agent_state_from_dict():
    agent = Agent(initial_state={"system_prompt": "hello", "messages": []})
    assert agent.state.system_prompt == "hello"


def test_agent_state_from_model():
    state = AgentState(system_prompt="hello")
    agent = Agent(initial_state=state)
    assert agent.state.system_prompt == "hello"
