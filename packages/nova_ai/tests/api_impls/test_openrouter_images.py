"""OpenRouter 图像 API：真实 openai SDK + respx 的线上形状契约。"""

import asyncio
import json

import httpx
import pytest
import respx
from nova_ai.api_impls.openrouter_images import generate_images
from nova_ai.stream_options import ImagesOptions
from nova_async import AbortController
from nova_protocol import (
    ClientAttribution,
    ClientIdentity,
    ImageContent,
    ImageModel,
    ImagesContext,
    ImagesStopReason,
    ModelCost,
    TextContent,
)

URL = "https://openrouter.ai/api/v1/chat/completions"


def _model(output_types):
    return ImageModel(
        id="example/image",
        name="Example Image",
        type="image",
        api="openrouter-images",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input_types=["text", "image"],
        output_types=output_types,
        cost=ModelCost(),
    )


def _response():
    return {
        "id": "img-1",
        "object": "chat.completion",
        "created": 0,
        "model": "example/image",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "images": [{"image_url": {"url": "data:image/png;base64,QQ=="}}],
                },
            }
        ],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 34,
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    }


@pytest.mark.parametrize(
    ("output_types", "modalities"),
    [(["image"], ["image"]), (["text", "image"], ["image", "text"])],
)
@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_request_and_response(output_types, modalities):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    context = ImagesContext(
        input=[
            TextContent(text="draw"),
            ImageContent(mime_type="image/png", data="QQ=="),
        ]
    )
    result = await generate_images(
        _model(output_types), context, ImagesOptions(api_key="test")
    )
    assert result.stop_reason is ImagesStopReason.STOP
    assert result.response_id == "img-1"
    assert result.output[0] == TextContent(text="done")
    assert result.output[1] == ImageContent(mime_type="image/png", data="QQ==")
    assert result.usage is not None
    assert result.usage.total_tokens == 46
    payload = json.loads(route.calls[0].request.content)
    assert payload["stream"] is False
    assert payload["modalities"] == modalities
    assert payload["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_pre_aborted_without_request():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    controller = AbortController()
    controller.abort()
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", signal=controller.signal),
    )
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_error_result_without_implicit_retry():
    route = respx.post(URL).mock(return_value=httpx.Response(500, text="broken"))
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test"),
    )
    assert result.stop_reason is ImagesStopReason.ERROR
    assert result.error_message
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_configured_retry_then_success():
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(500, text="broken"),
            httpx.Response(200, json=_response()),
        ]
    )
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", max_retries=1, max_retry_delay_ms=0),
    )
    assert result.stop_reason is ImagesStopReason.STOP
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_custom_payload_fields_reach_wire():
    """SDK 未声明的 OpenRouter 字段经 extra_body 上线，不进 kwargs。"""
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))

    def on_payload(params, _model):
        params["image_config"] = {"aspect_ratio": "16:9"}
        return params

    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", on_payload=on_payload),
    )
    assert result.stop_reason is ImagesStopReason.STOP, result.error_message
    payload = json.loads(route.calls[0].request.content)
    assert payload["image_config"] == {"aspect_ratio": "16:9"}


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_abort_interrupts_in_flight_request():
    started = asyncio.Event()
    never = asyncio.Event()

    async def hold_request(_request):
        started.set()
        await never.wait()
        return httpx.Response(200, json=_response())

    respx.post(URL).mock(side_effect=hold_request)
    controller = AbortController()
    task = asyncio.create_task(
        generate_images(
            _model(["image"]),
            ImagesContext(input=[TextContent(text="draw")]),
            ImagesOptions(api_key="test", signal=controller.signal),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert "raw_response" not in (result.error_message or "")


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_abort_inside_response_hook_is_not_success():
    respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    controller = AbortController()

    def on_response(_response, _model):
        controller.abort()

    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(
            api_key="test", signal=controller.signal, on_response=on_response
        ),
    )
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert result.error_message


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_headers_override_case_insensitively():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    model = _model(["image"]).model_copy(update={"headers": {"X-Trace": "model"}})
    result = await generate_images(
        model,
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", headers={"x-trace": "request"}),
    )
    assert result.stop_reason is ImagesStopReason.STOP
    assert route.calls[0].request.headers["x-trace"] == "request"


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_async_payload_hook_is_abortable():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    started = asyncio.Event()
    never = asyncio.Event()
    controller = AbortController()

    async def on_payload(_params, _model):
        started.set()
        await never.wait()

    task = asyncio.create_task(
        generate_images(
            _model(["image"]),
            ImagesContext(input=[TextContent(text="draw")]),
            ImagesOptions(
                api_key="test", signal=controller.signal, on_payload=on_payload
            ),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, timeout=1)
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_explicit_authorization_none_is_deleted():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="real-key", headers={"Authorization": None}),
    )
    assert result.stop_reason is ImagesStopReason.STOP, result.error_message
    assert "authorization" not in route.calls[0].request.headers


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_cache_read_and_write_are_independent():
    payload = _response()
    payload["usage"] = {
        "prompt_tokens": 100,
        "completion_tokens": 10,
        "prompt_tokens_details": {
            "cached_tokens": 50,
            "cache_write_tokens": 30,
        },
    }
    respx.post(URL).mock(return_value=httpx.Response(200, json=payload))
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test"),
    )
    assert result.usage is not None
    assert result.usage.input == 20
    assert result.usage.cache_read == 50
    assert result.usage.cache_write == 30
    assert result.usage.total_tokens == 110


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_abort_error_is_redacted():
    from nova_async import AbortedError

    async def on_payload(_params, _model):
        raise AbortedError("Bearer sk-live-FAKE-REVIEW-SECRET")

    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", on_payload=on_payload),
    )
    assert result.stop_reason is ImagesStopReason.ABORTED
    assert "sk-live-FAKE-REVIEW-SECRET" not in (result.error_message or "")
    assert route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_images_identity_headers_reach_wire():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=_response()))
    identity = ClientIdentity(
        user_agent="short",
        attribution=ClientAttribution(
            name="nova-test", url="https://example.com", categories=["cli-agent"]
        ),
    )
    result = await generate_images(
        _model(["image"]),
        ImagesContext(input=[TextContent(text="draw")]),
        ImagesOptions(api_key="test", identity=identity),
    )
    assert result.stop_reason is ImagesStopReason.STOP
    headers = route.calls[0].request.headers
    assert headers["user-agent"] == "nova-ai"
    assert headers["http-referer"] == "https://example.com"
    assert headers["x-openrouter-title"] == "nova-test"
