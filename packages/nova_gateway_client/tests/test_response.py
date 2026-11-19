"""响应元数据、HTTP 注入与默认配置测试。"""

import httpx
import pytest
import respx

from nova_gateway_client import NovaGatewayClient

pytestmark = pytest.mark.asyncio


def _models_payload():
    return {
        "models": [
            {
                "provider": "test-provider",
                "id": "model-a",
                "name": "Model A",
                "type": "chat",
                "reasoning": True,
                "inputTypes": ["text"],
            }
        ]
    }


@respx.mock
async def test_list_models_response_exposes_http_metadata():
    respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(
            200,
            headers={"x-request-id": "req-1"},
            json=_models_payload(),
        )
    )
    async with NovaGatewayClient("https://gateway.test", "token") as client:
        result = await client.list_models_with_response()
    assert result.status_code == 200
    assert result.request_id == "req-1"
    assert result.request.method == "GET"
    assert result.retries_taken == 0
    assert result.data.models[0].id == "model-a"


@respx.mock
async def test_default_headers_are_sent():
    route = respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(200, json=_models_payload())
    )
    async with NovaGatewayClient(
        "https://gateway.test",
        "token",
        default_headers={"X-Test": "configured"},
    ) as client:
        await client.list_models()
    assert route.calls[0].request.headers["x-test"] == "configured"


@respx.mock
async def test_injected_http_client_is_not_closed():
    http_client = httpx.AsyncClient()
    respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(200, json=_models_payload())
    )
    client = NovaGatewayClient("https://gateway.test", "token", http_client=http_client)
    await client.list_models()
    await client.close()
    assert not http_client.is_closed
    await http_client.aclose()
