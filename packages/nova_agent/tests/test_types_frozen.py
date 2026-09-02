"""
类型语义测试

- 不可变性：事件与配置是值对象，构造后不可变（按根 AGENTS.md 数据建模规则）。
- AgentState 拷贝语义：tools/messages 赋值时拷贝顶层数组（对齐 TS MutableAgentState）。
"""

import dataclasses

import pytest
from helpers import EchoTool, SlowTool
from nova_ai import Model, SimpleStreamOptions, UserMessage

from nova_agent import (
    AgentEndEvent,
    AgentLoopConfig,
    AgentStartEvent,
    AgentState,
    BeforeToolCallResult,
    MessageStartEvent,
    TurnEndEvent,
)
from nova_agent.types.tool_execution import ExecutedToolCallBatch


def test_agent_event_is_frozen():
    """事件构造后字段不可重新赋值。"""
    event = AgentStartEvent()
    with pytest.raises(Exception):
        event.type = "hacked"


def test_message_event_is_frozen():
    event = MessageStartEvent(message=UserMessage(role="user", content="hi"))
    with pytest.raises(Exception):
        event.message = UserMessage(role="user", content="other")


def test_hook_result_is_frozen():
    """hook 返回值对象构造后不可变。"""
    result = BeforeToolCallResult(block=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.block = False


def test_tool_call_batch_is_frozen():
    batch = ExecutedToolCallBatch()
    with pytest.raises(dataclasses.FrozenInstanceError):
        batch.terminate = True


def test_agent_loop_config_is_frozen(dummy_model):
    """AgentLoopConfig 构造后不可变，循环内更新走 dataclasses.replace。"""
    config = AgentLoopConfig(stream_options=SimpleStreamOptions(), model=dummy_model)
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.tool_execution = "sequential"

    # replace 仍然可用（frozen 与不可变更新路径兼容）
    replaced = dataclasses.replace(config, tool_execution="sequential")
    assert replaced.tool_execution == "sequential"
    assert config.tool_execution == "parallel"


# ------------------------------------------------------------------------------
# AgentState 拷贝语义（对齐 TS MutableAgentState 的 accessor 拷贝）
# ------------------------------------------------------------------------------


def test_agent_state_tools_assignment_copies_list():
    """tools 赋值时拷贝顶层数组：内外两个数组互不影响。"""
    state = AgentState()
    tools = [EchoTool()]
    state.tools = tools

    tools.append(SlowTool())
    assert len(state.tools) == 1  # 外部 append 不影响内部

    state.tools.append(SlowTool())
    assert len(tools) == 2  # 内部 append 不影响外部（外部恰为 2）


def test_agent_state_messages_assignment_copies_list():
    """messages 赋值时拷贝顶层数组：外部清空原数组不影响内部状态。"""
    state = AgentState()
    messages = [UserMessage(role="user", content="a")]
    state.messages = messages

    messages.clear()
    assert len(state.messages) == 1
    assert state.messages[0].content == "a"


def test_agent_state_constructor_copies_initial_lists(dummy_model):
    """构造时传入的 tools/messages 同样被拷贝，不与外部共享引用。"""
    tools = [EchoTool()]
    messages = [UserMessage(role="user", content="a")]
    state = AgentState(model=dummy_model, tools=tools, messages=messages)

    tools.clear()
    messages.clear()
    assert len(state.tools) == 1
    assert len(state.messages) == 1
