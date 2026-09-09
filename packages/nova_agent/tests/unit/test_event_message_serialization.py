"""事件消息字段序列化回归测试。

锁定：``AgentMessage = Union[Message, CustomAgentMessage]`` 的事件字段必须按
**运行时类型**序列化——无字段的 ``CustomAgentMessage`` 基类会把子类剥成
``{}``（扩展注入消息、bash 用户工具终态卡经 RPC 到不了前端的根因）。
"""

from typing import Literal

from nova_agent.types.base import CustomAgentMessage
from nova_agent.types.events import (
    AgentEndEvent,
    MessageEndEvent,
    MessageStartEvent,
)


class _FakeCustomMessage(CustomAgentMessage):
    """模拟 harness 侧的 custom 消息（CustomMessage/BashExecutionMessage 同族）。"""

    custom_type: str
    content: str
    role: Literal["custom"] = "custom"


class _FakeBashMessage(CustomAgentMessage):
    command: str
    output: str
    exit_code: int
    role: Literal["bashExecution"] = "bashExecution"


def test_message_end_keeps_custom_subclass_fields():
    event = MessageEndEvent(
        message=_FakeCustomMessage(custom_type="info", content="用法: /import <path>")
    )
    dumped = event.dump_wire()
    message = dumped["message"]
    assert message["role"] == "custom"
    assert message["customType"] == "info"  # camel 别名
    assert message["content"] == "用法: /import <path>"


def test_message_end_keeps_named_role_subclass_fields():
    event = MessageEndEvent(
        message=_FakeBashMessage(command="ls", output="a\nb", exit_code=0)
    )
    message = event.dump_wire()["message"]
    assert message["role"] == "bashExecution"
    assert message["command"] == "ls"
    assert message["exitCode"] == 0


def test_message_start_same_serializer():
    event = MessageStartEvent(message=_FakeCustomMessage(custom_type="x", content="c"))
    assert event.dump_wire()["message"]["customType"] == "x"


def test_agent_end_messages_list_keeps_fields():
    event = AgentEndEvent(
        messages=[_FakeCustomMessage(custom_type="info", content="c1")]
    )
    messages = event.dump_wire()["messages"]
    assert messages[0]["customType"] == "info"
    assert messages[0]["content"] == "c1"
