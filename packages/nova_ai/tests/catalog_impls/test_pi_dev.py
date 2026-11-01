"""PiDevCatalog 测试（三态/条件请求/字段翻译边缘面）。"""

import time

import httpx
import pytest
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
            "cost": {"input": 3, "output": 15, "cacheRead": 0.3, "cacheWrite": 0},
        },
        "bad": {"name": "missing-id"},
    }


class TestPiDevCatalog:
    async def test_200_maps_fields_and_extracts_validators(self, monkeypatch):
        seen = {}

        async def fake_request(method, url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(
                200,
                json=_pi_payload(),
                headers={
                    "etag": '"v9"',
                    "last-modified": "Wed, 01 Jan 2025 00:00:00 GMT",
                },
            )

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
        assert outcome.status == 200
        assert outcome.etag == '"v9"'
        assert outcome.last_modified == 1735689600000
        # validator 以 if-none-match 发送
        assert seen["headers"]["if-none-match"] == '"v8"'
        # 畸形条目跳过，camelCase 已翻译
        assert len(outcome.models_fields) == 1
        fields = outcome.models_fields[0]
        assert fields["id"] == "kimi-k3"
        assert fields["context_window"] == 262144
        assert fields["cost"]["cache_read"] == 0.3

    async def test_no_validator_no_conditional_header(self, monkeypatch):
        seen = {}

        async def fake_request(method, url, **kwargs):
            seen["headers"] = kwargs.get("headers")
            return httpx.Response(200, json={})

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        await PiDevCatalog().fetch("kimi-coding", None, None)
        assert "if-none-match" not in seen["headers"]

    async def test_304_passes_through(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(304)

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("kimi-coding", None, '"v8"')
        assert outcome.status == 304
        assert outcome.models_fields == []

    async def test_404_means_no_catalog(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(404)

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("volcengine", None, None)
        assert outcome.status == 404

    async def test_failure_synthesizes_status_0(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_pre_aborted_returns_0_without_request(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            raise AssertionError("aborted 信号下不应发请求")

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        controller = AbortController(name="test")
        controller.abort()
        outcome = await PiDevCatalog().fetch("kimi-coding", controller.signal, None)
        assert outcome.status == 0

    async def test_malformed_json_is_status_0(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(200, content=b"not-json")

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_non_dict_payload_is_status_0(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(200, json=[1, 2])

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 0

    async def test_501_means_no_catalog(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(501)

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("nope", None, None)
        assert outcome.status == 501

    async def test_missing_last_modified_falls_back_to_now(self, monkeypatch):
        async def fake_request(method, url, **kwargs):
            return httpx.Response(200, json={}, headers={"etag": '"v1"'})

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        before = int(time.time() * 1000)
        outcome = await PiDevCatalog().fetch("kimi-coding", None, None)
        assert outcome.status == 200
        assert outcome.last_modified is not None
        assert before <= outcome.last_modified <= int(time.time() * 1000)

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

        async def fake_request(method, url, **kwargs):
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(
            "nova_ai.catalog_impls.pi_dev.request_with_abort", fake_request
        )
        outcome = await PiDevCatalog().fetch("p1", None, None)
        fields = outcome.models_fields[0]
        assert fields["max_tokens"] == 100000
        assert fields["thinking_level_map"] == {"high": "high"}
        assert fields["headers"] == {"X-A": "b"}
        assert fields["compat"] == {"thinking_format": "openai"}
