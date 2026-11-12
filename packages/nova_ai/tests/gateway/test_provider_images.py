"""Provider 的混合目录读取与图像实现分派测试。"""

import pytest
from nova_protocol import ImageModel, ImagesContext, ImagesStopReason, TextContent

from nova_ai.gateway import create_provider
from nova_ai.stream_options import ImagesOptions
from tests.gateway._helpers.images import FakeImages, _chat, _image


def test_provider_separates_chat_and_image_reads_with_same_id():
    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_chat(), _image()],
        images={"openrouter-images": images},
    )
    assert [model.id for model in provider.get_models()] == ["shared"]
    assert len(provider.get_all_models()) == 2
    chat = provider.get_model("shared")
    image = provider.get_model_of_type("image", "shared")
    assert chat is not None
    assert chat.type == "chat"
    assert isinstance(image, ImageModel)
    assert image.output_types == ["image"]


def test_openrouter_builtin_is_image_only():
    from nova_ai.providers import builtin_models

    manager = builtin_models()
    assert manager.get_models("openrouter") == []
    images = manager.get_models_of_type("image", "openrouter")
    assert images
    assert all(isinstance(model, ImageModel) for model in images)
    provider = manager.get_provider("openrouter")
    assert provider is not None
    assert provider.generate_images is not None


@pytest.mark.asyncio
async def test_provider_image_implementations_are_detached_from_input_dict():
    from nova_ai.gateway import ProviderImages

    images = FakeImages()
    implementations: dict[str, ProviderImages] = {"openrouter-images": images}
    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images=implementations,
    )
    implementations.clear()

    assert provider.generate_images is not None
    result = await provider.generate_images(
        _image(), ImagesContext(input=[TextContent(text="draw")])
    )
    assert result.stop_reason is ImagesStopReason.STOP
    assert len(images.calls) == 1


@pytest.mark.asyncio
async def test_image_dispatch_normalizes_known_api_enum_values():
    from nova_protocol import KnownApi

    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openai-completions": images},
    )
    model = _image().model_copy(update={"api": KnownApi.OPENAI_COMPLETIONS})
    assert provider.generate_images is not None
    result = await provider.generate_images(
        model, ImagesContext(input=[TextContent(text="draw")])
    )
    assert result.stop_reason is ImagesStopReason.STOP
