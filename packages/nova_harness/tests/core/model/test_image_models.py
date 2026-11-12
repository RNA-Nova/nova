"""harness 组合层的图像目录与 generate_images 能力透传。"""

import pytest
from nova_harness.model.composer import compose_provider
from nova_protocol import (
    AssistantImages,
    ImageModel,
    ImagesContext,
    ImagesStopReason,
    ModelCost,
    TextContent,
)

from nova_ai import create_provider


class FakeImages:
    def __init__(self):
        self.calls = 0

    async def generate_images(self, model, context, options=None):
        self.calls += 1
        return AssistantImages(
            api=model.api,
            provider=model.provider,
            model=model.id,
            output=[TextContent(text="ok")],
            stop_reason=ImagesStopReason.STOP,
        )


def _image():
    return ImageModel(
        id="image",
        name="Image",
        type="image",
        api="openrouter-images",
        provider="test",
        base_url="https://example.com/v1",
        input_types=["text"],
        output_types=["image"],
        cost=ModelCost(),
    )


def test_composed_provider_preserves_image_catalog_and_capability():
    images = FakeImages()
    base = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openrouter-images": images},
    )
    composed = compose_provider("test", base, None, None)
    assert composed.get_models() == []
    assert [model.id for model in composed.get_all_models()] == ["image"]
    assert composed.generate_images is not None


@pytest.mark.asyncio
async def test_composed_provider_delegates_image_generation():
    images = FakeImages()
    base = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openrouter-images": images},
    )
    composed = compose_provider("test", base, None, None)
    generate = composed.generate_images
    assert generate is not None
    result = await generate(_image(), ImagesContext(input=[TextContent(text="draw")]))
    assert result.stop_reason is ImagesStopReason.STOP
    assert images.calls == 1
