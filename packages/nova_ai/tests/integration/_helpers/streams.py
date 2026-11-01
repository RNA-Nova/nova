"""流式测试的公共提取件。"""

from typing import List

from nova_protocol import AssistantMessage


def extract_text(message: AssistantMessage) -> str:
    return "".join(c.text for c in message.content if c.type == "text")


def extract_thinking(message: AssistantMessage) -> str:
    return "".join(c.thinking for c in message.content if c.type == "thinking")


async def collect_event_types(event_stream) -> List[str]:
    types: List[str] = []
    async for event in event_stream:
        types.append(event.type)
    return types
