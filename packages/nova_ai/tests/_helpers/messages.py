"""AI 测试中的消息与内容块运行时窄化。"""

from nova_protocol import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
)


def assistant_message(message: Message) -> AssistantMessage:
    assert isinstance(message, AssistantMessage), type(message).__name__
    return message


def tool_result_message(message: Message) -> ToolResultMessage:
    assert isinstance(message, ToolResultMessage), type(message).__name__
    return message


def tool_call(message: Message, index: int = 0) -> ToolCall:
    assistant = assistant_message(message)
    block = assistant.content[index]
    assert isinstance(block, ToolCall), type(block).__name__
    return block


def text_of(message: Message, index: int = 0) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    block = content[index]
    assert isinstance(block, TextContent), type(block).__name__
    return block.text
