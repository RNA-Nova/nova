"""OpenRouter 图像生成 e2e：完整目录 → 鉴权 → 非流式图像结果链。"""

import asyncio
import base64

import pytest
from nova_protocol import (
    ImageContent,
    ImageModel,
    ImagesContext,
    ImagesStopReason,
    TextContent,
)

from nova_ai.stream_options import ImagesOptions

from ._helpers.auth import build_manager, has_credential
from ._helpers.probes import probe_for

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]


# 实测兼容 chat/completions 的图像模型；flux-3-image 仅支持独立 /images，
# 不能作为本实现的探针（否则 404 与测试实现无关）。
_CHAT_COMPLETIONS_IMAGE_PROBE = "black-forest-labs/flux.2-klein-4b"


async def test_openrouter_generates_decodable_image():
    if not has_credential("openrouter"):
        pytest.skip("OpenRouter credentials are not configured")
    manager = await build_manager()
    model = probe_for(
        manager.get_models_of_type("image", "openrouter"),
        require=lambda candidate: candidate.id == _CHAT_COMPLETIONS_IMAGE_PROBE,
    )
    if model is None:
        pytest.skip("chat/completions 兼容的图像探针不在当前目录中")
    assert isinstance(model, ImageModel)
    context = ImagesContext(
        input=[
            TextContent(
                text="Generate a simple solid red circle on a plain white background. No text."
            )
        ]
    )
    async with asyncio.timeout(180):
        result = await manager.generate_images(
            model,
            context,
            ImagesOptions(timeout=120, max_retries=0),
        )
    assert result.stop_reason is ImagesStopReason.STOP, result.error_message
    image = next(
        (item for item in result.output if isinstance(item, ImageContent)), None
    )
    assert image is not None
    assert image.mime_type.startswith("image/")
    decoded = base64.b64decode(image.data, validate=True)
    assert len(decoded) > 100
