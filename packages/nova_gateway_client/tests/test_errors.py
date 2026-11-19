"""具名 HTTP、连接与协议异常测试。"""

import httpx
import pytest
import respx
from nova_protocol import NovaErrorKind

from nova_gateway_client import (
    GatewayConnectionError,
    GatewayRateLimitError,
    NovaGatewayClient,
)

pytestmark = pytest.mark.asyncio


@respx.mock
async def test_http_error_exposes_kind_body_retry_and_request_id():
    respx.get("https://gateway.test/api/models").mock(
        return_value=httpx.Response(
            429,
            headers={"Retry-After": "2", "x-request-id": "req-1"},
            json={"error": {"message": "slow down", "kind": "rateLimited"}},
        )
    )
    async with NovaGatewayClient(
        "https://gateway.test", "token", max_retries=0
    ) as client:
        with pytest.raises(GatewayRateLimitError) as caught:
            await client.list_models()
    error = caught.value
    assert error.status_code == 429
    assert error.kind is NovaErrorKind.RATE_LIMITED
    assert error.retry_after_ms == 2000
    assert error.request_id == "req-1"
    assert error.body["error"]["message"] == "slow down"


@respx.mock
async def test_connection_error_preserves_request():
    respx.get("https://gateway.test/api/models").mock(
        side_effect=httpx.ConnectError("boom")
    )
    async with NovaGatewayClient(
        "https://gateway.test", "token", max_retries=0
    ) as client:
        with pytest.raises(GatewayConnectionError) as caught:
            await client.list_models()
    assert caught.value.request.method == "GET"
    assert str(caught.value.request.url) == "https://gateway.test/api/models"
