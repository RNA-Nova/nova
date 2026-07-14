"""blockImages 设置测试。"""

from typing import Any, List

import pytest

from nova_ai import AssistantMessage, ImageContent, TextContent, UserMessage
from nova_harness.core.agent_session.factory import create_convert_to_llm


class _FakeSettingsManager:
    def __init__(self, block_images: bool = False) -> None:
        self._block_images = block_images

    def get_block_images(self) -> bool:
        return self._block_images


async def _identity_convert(messages: List[Any]) -> List[Any]:
    return list(messages)


def _make_user_message(content: Any) -> UserMessage:
    return UserMessage(role="user", content=content)


@pytest.mark.asyncio
async def test_block_images_disabled_passthrough(monkeypatch) -> None:
    """block_images=false 时原样透传。"""
    settings = _FakeSettingsManager(block_images=False)
    convert = create_convert_to_llm(settings)

    original = _make_user_message(
        [
            TextContent(type="text", text="hello"),
            ImageContent(type="image", mime_type="image/png", data="base64"),
        ]
    )
    result = await convert([original])

    assert len(result) == 1
    assert result[0] is original


@pytest.mark.asyncio
async def test_block_images_enabled_replaces_image(monkeypatch) -> None:
    """block_images=true 时图片被替换为占位文本。"""
    settings = _FakeSettingsManager(block_images=True)
    convert = create_convert_to_llm(settings)

    original = _make_user_message(
        [
            TextContent(type="text", text="hello"),
            ImageContent(type="image", mime_type="image/png", data="base64"),
        ]
    )
    result = await convert([original])

    assert len(result) == 1
    assert result[0] is not original
    assert result[0].role == "user"
    blocks = result[0].content
    assert len(blocks) == 2
    assert blocks[0].type == "text"
    assert blocks[0].text == "hello"
    assert blocks[1].type == "text"
    assert blocks[1].text == "Image reading is disabled."


@pytest.mark.asyncio
async def test_block_images_dedupes_consecutive_placeholders() -> None:
    """连续多个图片只保留一个占位文本。"""
    settings = _FakeSettingsManager(block_images=True)
    convert = create_convert_to_llm(settings)

    original = _make_user_message(
        [
            ImageContent(type="image", mime_type="image/png", data="a"),
            ImageContent(type="image", mime_type="image/png", data="b"),
        ]
    )
    result = await convert([original])

    blocks = result[0].content
    assert len(blocks) == 1
    assert blocks[0].text == "Image reading is disabled."


@pytest.mark.asyncio
async def test_block_images_preserves_non_user_roles() -> None:
    """非 user/toolResult 角色不处理。"""
    settings = _FakeSettingsManager(block_images=True)
    convert = create_convert_to_llm(settings)

    original = AssistantMessage(
        role="assistant",
        content=[TextContent(type="text", text="assistant reply")],
    )
    result = await convert([original])

    assert result[0] is original


@pytest.mark.asyncio
async def test_block_images_string_content_passthrough() -> None:
    """字符串内容直接透传。"""
    settings = _FakeSettingsManager(block_images=True)
    convert = create_convert_to_llm(settings)

    original = _make_user_message("plain text")
    result = await convert([original])

    assert result[0] is original
