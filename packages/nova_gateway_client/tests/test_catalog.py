"""模型列表与公共目录客户端测试。"""

import json

import httpx
import pytest
import respx
from nova_protocol import Model

from nova_gateway_client import NovaGatewayClient

pytestmark = pytest.mark.asyncio


def _model_payload():
    return {
        "id": "model-a",
        "name": "Model A",
        "api": "openai-completions",
        "provider": "test-provider",
        "baseUrl": "https://upstream.example/v1",
        "reasoning": True,
        "input": ["text"],
        "cost": {"input": 1, "output": 2, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 128000,
        "maxTokens": 8192,
        "type": "chat",
    }


@respx.mock
async def test_list_models_uses_typed_response():
    route = respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "provider": "test-provider",
                        "id": "model-a",
                        "name": "Model A",
                        "type": "chat",
                        "reasoning": True,
                        "inputTypes": ["text"],
                        "contextWindow": 128000,
                        "maxTokens": 8192,
                        "available": True,
                    }
                ]
            },
        )
    )
    result = await NovaGatewayClient("https://gateway.test", "token").list_models()
    assert len(result.models) == 1
    assert result.models[0].id == "model-a"
    assert result.models[0].available is True
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer token"
    assert request.headers["x-nova-gateway-protocol"] == "1.2"


@respx.mock
async def test_catalog_parses_typed_models_and_validators():
    route = respx.get("https://gateway.test/api/models/providers/test-provider").mock(
        return_value=httpx.Response(
            200,
            content=json.dumps([_model_payload()]).encode(),
            headers={
                "ETag": '"catalog-v1"',
                "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT",
            },
        )
    )
    result = await NovaGatewayClient("https://gateway.test").catalog(
        "test-provider", validator='"old"'
    )
    assert result.status == 200
    assert len(result.models) == 1
    assert isinstance(result.models[0], Model)
    assert result.models[0].id == "model-a"
    assert result.etag == '"catalog-v1"'
    assert result.last_modified == 1735689600000
    request = route.calls[0].request
    assert request.headers["if-none-match"] == '"old"'
    assert request.url.params["types"] == "chat,image,classifier"


@respx.mock
async def test_catalog_supports_legacy_keyed_chat_payload():
    respx.get("https://gateway.test/api/models/providers/test-provider").mock(
        return_value=httpx.Response(200, json={"model-a": _model_payload()})
    )
    result = await NovaGatewayClient("https://gateway.test").catalog("test-provider")
    assert result.status == 200
    assert result.models[0].id == "model-a"


@respx.mock
async def test_catalog_returns_304_without_rebuilding_models():
    respx.get("https://gateway.test/api/models/providers/test-provider").mock(
        return_value=httpx.Response(304)
    )
    result = await NovaGatewayClient("https://gateway.test").catalog(
        "test-provider", validator='"catalog-v1"'
    )
    assert result.status == 304
    assert result.models == []
    assert result.etag == '"catalog-v1"'


@respx.mock
async def test_catalog_returns_404_for_unknown_provider():
    respx.get("https://gateway.test/api/models/providers/missing").mock(
        return_value=httpx.Response(404)
    )
    result = await NovaGatewayClient("https://gateway.test").catalog("missing")
    assert result.status == 404
    assert result.models == []
