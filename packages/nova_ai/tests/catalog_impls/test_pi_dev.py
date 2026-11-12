"""PiDevCatalog 测试（三态/条件请求/字段翻译边缘面）。"""

import time

import httpx
import pytest
import respx
from nova_protocol import AbortController

from nova_ai.catalog_impls.pi_dev import PiDevCatalog

pytestmark = pytest.mark.asyncio


def _pi_payload():
    return {
        "kimi-k3": {
            "id": "kimi-k3",
            "name": "Kimi K3",
            "contextWindow": 262144,
            "maxTokens": 32768,
            "reasoning": True,
            "input": ["text"],
            "cost": {
                "input": 3,
                "output": 15,
                "cacheRead": 0.3,
                "cacheWrite": 0,
                "tiers": [
                    {
                        "inputTokensAbove": 200000,
                        "input": 6,
                        "output": 30,
                        "cacheRead": 0.6,
                        "cacheWrite": 0,
                    }
                ],
            },
            "samplingParams": {"temperature": 0.2},
            "samplingParamsByThinkingLevel": {"high": {"top_k": 8}},
        },
        "bad": {"name": "missing-id"},
    }


class TestPiDevCatalog:
    @respx.mock
    async def test_transient_response_is_retried_before_status_dispatch(
        self, monkeypatch
    ):
        async def no_sleep(_milliseconds, _signal=None):
            return None

        monkeypatch.setattr("nova_ai.utils.http.abortable_sleep", no_sleep)
        route = respx.get(
            "https://pi.dev/api/models/providers/test?types=chat,image,classifier"
        ).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(200, json={}),
            ]
        )
        outcome = await PiDevCatalog().fetch("test", None, None)
        assert outcome.status == 200
        assert route.call_count == 2

    @respx.mock
    async def test_provider_key_is_encoded_as_one_path_segment(self):
        route = respx.route(method="GET").mock(return_value=httpx.Response(404))
        await PiDevCatalog().fetch("tenant/a?admin=true#fragment", None, None)
        request = route.calls[0].request
        assert request.url.path == "/api/models/providers/tenant/a?admin=true#fragment"
        assert request.url.query == b"types=chat,image,classifier"

    async def test_200_maps_fields_and_extracts_validators(self, monkeypatch):
        seen = {}

        async def fake_request(url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(
                200,
                json=_pi_payload(),
                headers={
                    "etag": '"v9"',
                    "last-modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                },
            )

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
        assert outcome.status == 200
        assert outcome.etag == '"v9"'
        assert outcome.last_modified == 1735689600000
        # validator 以 if-none-match 发送
        assert seen["headers"]["if-none-match"] == '"v8"'
        # 畸形条目跳过，camelCase 已翻译
        assert len(outcome.models_fields) == 1
        fields = outcome.models_fields[0]
        assert fields.get("id") == "kimi-k3"
        assert fields.get("context_window") == 262144
        cost = fields.get("cost")
        assert cost is not None
        assert cost.get("cache_read") == 0.3
        tiers = cost.get("tiers")
        assert tiers is not None
        assert tiers[0] == {
            "inputTokensAbove": 200000,
            "input": 6,
            "output": 30,
            "cache_read": 0.6,
            "cache_write": 0,
        }
        assert fields.get("sampling_params") == {"temperature": 0.2}
        assert fields.get("sampling_params_by_thinking_level") == {"high": {"top_k": 8}}

    async def test_no_validator_no_conditional_header(self, monkeypatch):
        seen = {}

        async def fake_request(url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(200, json={})

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        await PiDevCatalog().fetch("kimi-coding", None, None)
        assert "if-none-match" not in seen["headers"]

    async def test_304_passes_through(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(304)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
        assert outcome.status == 304
        assert outcome.models_fields == []

    async def test_404_means_no_catalog(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(404)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 404

    async def test_failure_synthesizes_status_0(self, monkeypatch):
        async def fake_request(url, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_pre_aborted_returns_0_without_request(self, monkeypatch):
        async def fake_request(url, **kwargs):
            raise AssertionError("aborted 信号下不应发请求")

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        controller = AbortController(name="test")
        controller.abort()
        outcome = await PiDevCatalog().fetch("kimi-coding", controller.signal, None)
        assert outcome.status == 0

    async def test_malformed_json_is_status_0(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(200, content=b"not-json")

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_invalid_payload_is_status_0(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(200, json=1)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_501_means_no_catalog(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(501)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("nope", None, None)
        assert outcome.status == 501

    async def test_missing_last_modified_stays_unknown(self, monkeypatch):
        async def fake_request(url, **kwargs):
            return httpx.Response(200, json={}, headers={"etag": '"v1"'})

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 200
        assert outcome.last_modified is None

    async def test_optional_fields_and_max_tokens_fallback(self, monkeypatch):
        payload = {
            "m1": {
                "id": "m1",
                "contextWindow": 100000,
                # maxTokens 缺席 → 回落 context_window
                "thinkingLevelMap": {"high": "high"},
                "headers": {"X-A": "b"},
                "compat": {"thinking_format": "openai"},
            }
        }

        async def fake_request(url, **kwargs):
            return httpx.Response(200, json=payload)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("p1", None, None)
        fields = outcome.models_fields[0]
        assert fields.get("max_tokens") == 100000
        assert fields.get("thinking_level_map") == {"high": "high"}
        assert fields.get("headers") == {"X-A": "b"}
        assert fields.get("compat") == {"thinking_format": "openai"}

    async def test_chat_boolean_windows_are_never_coerced_to_one(self, monkeypatch):
        payload = {
            "bad-context": {"id": "bad-context", "contextWindow": True},
            "valid": {
                "id": "valid",
                "contextWindow": 1000,
                "maxTokens": True,
            },
        }

        async def fake_request(url, **kwargs):
            return httpx.Response(200, json=payload)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("p1", None, None)
        assert len(outcome.models_fields) == 1
        fields = outcome.models_fields[0]
        assert fields.get("id") == "valid"
        assert fields.get("context_window") == 1000
        assert fields.get("max_tokens") == 1000

    @pytest.mark.parametrize("form", ["array", "wrapped", "keyed"])
    async def test_typed_catalog_forms_operations_and_base_url(self, monkeypatch, form):
        chat = {
            "id": "chat",
            "type": "chat",
            "contextWindow": 1000,
            "maxTokens": 100,
            "api": "anthropic-messages",
            "baseUrl": "https://openrouter.ai/api",
            "cost": {},
        }
        image = {
            "id": "image",
            "type": "image",
            "api": "openrouter-images",
            "baseUrl": "https://images.example/v1",
            "input": ["text"],
            "output": ["image"],
            "cost": {},
        }
        classifier = {
            "id": "classifier",
            "type": "classifier",
            "api": "typesafe-system-one",
            "baseUrl": "https://classify.example/v1",
            "input": ["text", "image"],
            "contextWindow": 32000,
            "compat": {
                "imageInput": "state-image-urls",
                "sendImageOptInHeader": False,
            },
            "cost": {},
        }
        invalid = [
            {**chat, "id": "empty", "type": ""},
            {**chat, "id": "null", "type": None},
            {**chat, "id": "future", "type": "future"},
        ]
        entries = [chat, image, classifier, *invalid]
        payload = (
            entries
            if form == "array"
            else (
                {"models": entries}
                if form == "wrapped"
                else {entry["id"]: entry for entry in entries}
            )
        )
        seen = {}

        async def fake_request(url, **kwargs):
            seen["url"] = url
            return httpx.Response(200, json=payload)

        monkeypatch.setattr("nova_ai.catalog_impls.pi_dev.get_with_retry", fake_request)
        outcome = await PiDevCatalog().fetch("openrouter", None, None)
        assert outcome.status == 200
        assert [
            (fields.get("type"), fields.get("id")) for fields in outcome.models_fields
        ] == [
            ("chat", "chat"),
            ("image", "image"),
            ("classifier", "classifier"),
        ]
        assert outcome.models_fields[0].get("base_url") == "https://openrouter.ai/api"
        assert outcome.models_fields[1].get("base_url") == "https://images.example/v1"
        assert outcome.models_fields[1].get("output_types") == ["image"]
        assert "context_window" not in outcome.models_fields[1]
        classifier_fields = outcome.models_fields[2]
        assert classifier_fields.get("context_window") == 32000
        assert classifier_fields.get("compat") == {
            "image_input": "state-image-urls",
            "send_image_opt_in_header": False,
        }
        assert "types=chat,image,classifier" in seen["url"]
