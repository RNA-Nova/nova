"""消息联合的测试侧运行时窄化与文本提取。"""

from typing import Iterable, List, Union

from nova_protocol import (
    AgentMessage,
    AgentToolResult,
    AssistantMessage,
    SystemMessage,
    TextContent,
    ToolResultMessage,
    UserMessage,
)


def assistant_message(message: AgentMessage) -> AssistantMessage:
    assert isinstance(message, AssistantMessage), type(message).__name__
    return message


def tool_result_message(message: AgentMessage) -> ToolResultMessage:
    assert isinstance(message, ToolResultMessage), type(message).__name__
    return message


def tool_result_messages(messages: Iterable[AgentMessage]) -> List[ToolResultMessage]:
    return [message for message in messages if isinstance(message, ToolResultMessage)]


def user_message(message: AgentMessage) -> UserMessage:
    assert isinstance(message, UserMessage), type(message).__name__
    return message


def system_message(message: AgentMessage) -> SystemMessage:
    assert isinstance(message, SystemMessage), type(message).__name__
    return message


def text_of(
    message: Union[
        AssistantMessage, ToolResultMessage, UserMessage, SystemMessage, AgentToolResult
    ],
    index: int = 0,
) -> str:
    if isinstance(message.content, str):
        return message.content
    block = message.content[index]
    assert isinstance(block, TextContent), type(block).__name__
    return block.text
