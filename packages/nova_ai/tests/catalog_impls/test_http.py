"""目录网络路径的 HTTP 级测试（respx transport 拦截——URL/请求头/序列化
全真跑，只有 socket 是假的；对位 codex wiremock 层）。

函数缝的假（monkeypatch request_with_abort）测逻辑分派；本文件测的是
"发出去的请求本身对不对"——两条缝互为补充，不互相替代。
"""

import httpx
import pytest
import respx
from nova_ai.catalog_impls.models_dev import MODELS_DEV_API, ModelsDevCatalog
from nova_ai.catalog_impls.pi_dev import PI_DEV_CATALOG_BASE_URL, PiDevCatalog
from nova_ai.gateway.provider import RefreshModelsContext
from nova_ai.providers.kimi_coding.catalog import BASE_URL, kimi_personalize
from nova_protocol import KnownApi, Model, ModelCost, OAuthCredential
from tests._helpers.auth import oauth_credential

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
    assert request.url.params["types"] == "chat,image,classifier"
    assert outcome.status == 200
    assert outcome.etag == '"v9"'
    assert outcome.models_fields[0].get("id") == "kimi-k3"


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
                            "type": "chat",
                            "capabilities": {"tools": {"supported": True}},
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
    assert outcome.models_fields[0].get("id") == "deepseek-v4"


def _kimi_base_model() -> Model:
    """覆盖式合并的基底模型（公共事实——compat/价格/max_tokens 继承源）。"""
    return Model(
        id="kimi-k3",
        name="Kimi K3",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="kimi-coding",
        base_url=BASE_URL,
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=0),
        context_window=1048576,
        max_tokens=131072,
    )


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
        credential=oauth_credential(access="tok", refresh="r", expires=9999999999999)
    )
    models = await kimi_personalize([_kimi_base_model()], context)
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tok"
    assert request.headers["accept"] == "application/json"
    # 覆盖式合并：managed 活字段生效（级别表来自 valid_efforts）
    assert [m.id for m in models] == ["kimi-k3"]
    merged = models[0]
    assert isinstance(merged, Model)
    levels = merged.thinking_level_map
    assert levels is not None
    assert levels.get("high") == "high"
