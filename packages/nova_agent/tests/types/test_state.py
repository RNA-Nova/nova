"""AgentState 可变容器的顶层数组拷贝契约。"""

from typing import List

from _helpers.agent import EchoTool, SlowTool
from nova_protocol import AgentMessage, UserMessage

from nova_agent import AgentState, AgentTool


def test_agent_state_tools_assignment_copies_list():
    state = AgentState()
    tools: List[AgentTool] = [EchoTool()]
    state.tools = tools

    tools.append(SlowTool())
    assert len(state.tools) == 1

    state.tools.append(SlowTool())
    assert len(tools) == 2


def test_agent_state_messages_assignment_copies_list():
    state = AgentState()
    messages: List[AgentMessage] = [UserMessage(role="user", content="a")]
    state.messages = messages

    messages.clear()
    assert state.messages == [UserMessage(role="user", content="a")]


def test_agent_state_constructor_copies_initial_lists(dummy_model):
    tools: List[AgentTool] = [EchoTool()]
    messages: List[AgentMessage] = [UserMessage(role="user", content="a")]
    state = AgentState(model=dummy_model, tools=tools, messages=messages)

    tools.clear()
    messages.clear()
    assert len(state.tools) == 1
    assert [message.role for message in state.messages] == ["system", "user"]
