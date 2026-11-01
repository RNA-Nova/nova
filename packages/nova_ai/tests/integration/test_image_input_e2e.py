"""图像输入 e2e：多模态消息契约（fixtures/test.png 真实样本）。

探针即每 provider 最便宜的视觉模型（input_types 含 image）。
"""

import asyncio
import base64
from pathlib import Path

import pytest
from nova_protocol import Context, ImageContent, TextContent, UserMessage

from ._helpers.auth import build_manager
from ._helpers.probes import collect_probes
from ._helpers.streams import extract_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),  # 真实网络 + 思考模型（默认 30s 是密封套件的）
]

_PROBES = collect_probes(require=lambda m: "image" in m.input_types)
_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test.png"


@pytest.mark.parametrize("probe", _PROBES, ids=lambda p: p.label)
async def test_image_input_accepted(probe):
    """图像 + 文本混合输入被接受并返回文本。"""
    manager = await build_manager()
    image_data = base64.b64encode(_FIXTURE.read_bytes()).decode("utf-8")
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="What is in this image?"),
                    ImageContent(mime_type="image/png", data=image_data),
                ]
            )
        ]
    )
    async with asyncio.timeout(120):
        result = await manager.complete_simple(probe.model, ctx)
    assert result.error_message is None
    assert len(extract_text(result)) > 0
