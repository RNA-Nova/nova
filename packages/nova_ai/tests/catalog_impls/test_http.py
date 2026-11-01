"""目录网络路径的 HTTP 级测试（respx transport 拦截——URL/请求头/序列化
全真跑，只有 socket 是假的；对位 codex wiremock 层）。

函数缝的假（monkeypatch request_with_abort）测逻辑分派；本文件测的是
"发出去的请求本身对不对"——两条缝互为补充，不互相替代。
"""

import httpx
import pytest
import respx
from nova_protocol import OAuthCredential

from nova_ai.catalog_impls.models_dev import MODELS_DEV_API, ModelsDevCatalog
from nova_ai.catalog_impls.pi_dev import PI_DEV_CATALOG_BASE_URL, PiDevCatalog
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.kimi_coding.catalog import kimi_coding_fetch_models

pytestmark = pytest.mark.asyncio


@respx.mock
async def test_pi_dev_request_line_and_conditional_headers():
    """pi.dev 条件请求：URL 按 provider id 拼路径，If-None-Match 如实发出。"""
    route = respx.get(
        f"{PI_DEV_CATALOG_BASE_URL}/api/models/providers/kimi-coding"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "kimi-k3": {
                    "id": "kimi-k3",
                    "contextWindow": 262144,
                    "maxTokens": 32768,
                    "cost": {"input": 3, "output": 15},
                }
            },
            headers={"etag": '"v9"'},
        )
    )
    outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
    assert route.called
    request = route.calls[0].request
    assert request.headers["if-none-match"] == '"v8"'
    assert request.headers["accept"] == "application/json"
    assert outcome.status == 200
    assert outcome.etag == '"v9"'
    assert outcome.models_fields[0]["id"] == "kimi-k3"


@respx.mock
async def test_pi_dev_304_short_circuits_body_parse():
    route = respx.get(
        f"{PI_DEV_CATALOG_BASE_URL}/api/models/providers/kimi-coding"
    ).mock(return_value=httpx.Response(304))
    outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
    assert route.called
    assert outcome.status == 304
    assert outcome.models_fields == []


@respx.mock
async def test_models_dev_request_line_and_catalog_ua():
    """models.dev 单体目录：URL 与目录 UA（CDN 拦截 Python 默认 UA 的规避）。"""
    route = respx.get(MODELS_DEV_API).mock(
        return_value=httpx.Response(
            200,
            json={
                "volcengine": {
                    "models": {
                        "deepseek-v4": {
                            "id": "deepseek-v4",
                            "name": "DeepSeek V4",
                            "limit": {"context": 1048576, "output": 393216},
                        }
                    }
                }
            },
        )
    )
    outcome = await ModelsDevCatalog().fetch("volcengine", None, None)
    assert route.called
    request = route.calls[0].request
    assert "nova-ai-model-data" in request.headers["user-agent"]
    assert outcome.status == 200
    assert outcome.models_fields[0]["id"] == "deepseek-v4"


@respx.mock
async def test_kimi_managed_sends_bearer_and_parses():
    """kimi managed /models：OAuth access 经 Authorization Bearer 真发出。"""
    route = respx.get("https://api.kimi.com/coding/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "kimi-k3",
                        "display_name": "Kimi K3",
                        "context_length": 262144,
                        "supports_reasoning": True,
                        "think_efforts": {"valid_efforts": ["low", "high"]},
                    }
                ]
            },
        )
    )
    context = RefreshModelsContext(
        credential=OAuthCredential(access="tok", refresh="r", expires=9999999999999)
    )
    models = await kimi_coding_fetch_models(context)
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["accept"] == "application/json"
    assert [m.id for m in models] == ["kimi-k3"]
    assert models[0].thinking_level_map is not None
    assert models[0].thinking_level_map.get("high") == "high"
