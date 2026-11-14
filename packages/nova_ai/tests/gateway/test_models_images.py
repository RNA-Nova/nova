"""ModelsManager 的图像鉴权、请求头与守卫顺序测试。"""

import pytest
from nova_ai.auth import ProviderAuth, env_api_key_auth
from nova_ai.gateway import ModelsManager, create_provider
from nova_ai.stream_options import ImagesOptions
from nova_protocol import ImagesContext, ImagesStopReason, Model, TextContent
from tests.gateway._helpers.images import FakeImages, _chat, _image


@pytest.mark.asyncio
async def test_manager_generates_images_with_auth_and_reports_capability_errors():
    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_chat(), _image()],
        images={"openrouter-images": images},
        auth=ProviderAuth(api_key=env_api_key_auth("Test", ["TEST_API_KEY"])),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    context = ImagesContext(input=[TextContent(text="draw")])
    result = await manager.generate_images(
        _image(), context, ImagesOptions(api_key="secret")
    )
    assert result.stop_reason is ImagesStopReason.STOP
    assert images.calls[0][2].api_key == "secret"
    assert images.calls[0][2].transform_headers is None

    not_image = await manager.generate_images(_chat(), context)
    assert not_image.stop_reason is ImagesStopReason.ERROR
    missing = await manager.generate_images(_image(provider="missing"), context)
    assert missing.stop_reason is ImagesStopReason.ERROR


@pytest.mark.asyncio
async def test_image_model_headers_resolver_is_abortable():
    import asyncio

    from nova_async import AbortController

    started = asyncio.Event()
    never = asyncio.Event()

    async def resolver(_model, _env):
        started.set()
        await never.wait()
        return None

    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openrouter-images": images},
        auth=ProviderAuth(api_key=env_api_key_auth("Test", ["TEST_API_KEY"])),
    )
    manager = ModelsManager(model_headers_resolver=resolver)
    manager.set_provider(provider)
    controller = AbortController()
    task = asyncio.create_task(
        manager.generate_images(
            _image(),
            ImagesContext(input=[TextContent(text="draw")]),
            ImagesOptions(api_key="secret", signal=controller.signal),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert images.calls == []


@pytest.mark.asyncio
async def test_image_transform_headers_is_abortable():
    import asyncio

    from nova_async import AbortController

    started = asyncio.Event()
    never = asyncio.Event()

    async def transform(_headers):
        started.set()
        await never.wait()
        return {}

    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openrouter-images": images},
        auth=ProviderAuth(api_key=env_api_key_auth("Test", ["TEST_API_KEY"])),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    controller = AbortController()
    task = asyncio.create_task(
        manager.generate_images(
            _image(),
            ImagesContext(input=[TextContent(text="draw")]),
            ImagesOptions(
                api_key="secret",
                signal=controller.signal,
                transform_headers=transform,
            ),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert images.calls == []


@pytest.mark.asyncio
async def test_image_transform_returns_the_final_header_set():
    images = FakeImages()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        images={"openrouter-images": images},
        auth=ProviderAuth(api_key=env_api_key_auth("Test", ["TEST_API_KEY"])),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    model = _image().model_copy(update={"headers": {"X-Remove": "model"}})
    result = await manager.generate_images(
        model,
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="secret", transform_headers=lambda _headers: {}),
    )
    assert result.stop_reason is ImagesStopReason.STOP
    called_model, _, options = images.calls[0]
    assert called_model.headers is None
    assert options.headers == {}


@pytest.mark.asyncio
async def test_manager_rejects_image_before_auth_side_effects():
    from typing import cast

    from nova_ai.auth import ApiKeyAuth
    from nova_protocol import Context, StopReason

    auth_calls = []

    async def resolve(_input):
        auth_calls.append("auth")
        return None

    provider = create_provider(
        id="test",
        name="Test",
        models=[_image()],
        auth=ProviderAuth(api_key=ApiKeyAuth(name="Test", resolve=resolve)),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    # 故意绕静态类型，验证 manager 的运行时守卫顺序。
    result = await manager.stream(cast(Model, _image()), Context()).result()
    assert result.stop_reason == StopReason.ERROR
    assert "not a chat model" in (result.error_message or "")
    assert auth_calls == []
