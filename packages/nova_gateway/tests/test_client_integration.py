"""公开协议、独立 SDK 与 AI 适配层的真实跨实现契约测试。"""

import pytest
from nova_ai import SimpleStreamOptions
from nova_ai.api_impls import nova_gateway
from nova_gateway.app import create_app
from nova_gateway.config import GatewayConfig
from nova_gateway.runtime import GatewayRuntime
from nova_gateway_client import NovaGatewayClient
from nova_gateway_protocol import GatewayDone, GatewayStart, ModelRef, StreamRequest
from nova_protocol import Context, DoneEvent, StartEvent, StopReason, UserMessage

from ._helpers import model, partial, provider, serve

pytestmark = pytest.mark.asyncio


@pytest.mark.enable_socket
@pytest.mark.timeout(15)
async def test_official_sdk_and_ai_adapter_interoperate_with_server():
    message = partial(model())
    provider_instance, _ = provider(
        [
            StartEvent(partial=message),
            DoneEvent(reason=StopReason.STOP, message=message),
        ]
    )
    runtime = GatewayRuntime(GatewayConfig(api_token="secret"))
    runtime.manager.set_provider(provider_instance)
    app = create_app(GatewayConfig(api_token="secret"), runtime=runtime)
    context = Context(messages=[UserMessage(content="hi")])
    async with serve(app) as base_url:
        async with NovaGatewayClient(base_url, "secret") as client:
            models = await client.list_models()
            assert models.models[0].id == "model-a"
            request = StreamRequest(
                model=ModelRef(provider="fake", id="model-a"),
                context=context,
            )
            events = [event async for event in client.stream(request)]
            assert isinstance(events[0], GatewayStart)
            assert isinstance(events[-1], GatewayDone)

        gateway_model = model().model_copy(update={"base_url": base_url})
        result = await nova_gateway.stream_simple(
            gateway_model,
            context,
            SimpleStreamOptions(api_key="secret"),
        ).result()
        assert result.stop_reason is StopReason.STOP
    await runtime.close()
