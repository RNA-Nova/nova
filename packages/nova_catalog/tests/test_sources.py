"""目录源翻译与 HTTP 边界的密封测试。"""

import httpx
import pytest
import respx
from nova_catalog.sources.ark import parse_ark_status
from nova_catalog.sources.http import FetchError, fetch_json
from nova_catalog.sources.models_dev import ModelsDevSource, models_dev_to_nova
from tests._helpers import raw_model


@pytest.mark.parametrize("payload", [{}, {"data": None}, {"data": {}}, []])
def test_ark_parser_rejects_missing_or_malformed_data(payload):
    assert parse_ark_status(payload) is None


def test_ark_parser_accepts_empty_list_and_extracts_bad_status():
    assert parse_ark_status({"data": []}) == {}
    assert parse_ark_status(
        {
            "data": [
                {"id": "a", "status": "Shutdown"},
                {"id": "b", "status": "Running"},
            ]
        }
    ) == {"a": "Shutdown"}


@pytest.mark.parametrize(
    ("field", "value"),
    [("capabilities", []), ("limit", [1]), ("modalities", "bad"), ("cost", [])],
)
def test_malformed_nested_shapes_degrade_or_fail_by_strictness(field, value):
    entry = raw_model()
    entry[field] = value
    models_dev_to_nova("test", entry)
    with pytest.raises(ValueError, match="must be"):
        models_dev_to_nova("test", entry, strict=True)


def test_limit_array_falls_back_to_default_windows():
    entry = raw_model()
    entry["limit"] = [1]
    fields = models_dev_to_nova("test", entry)
    assert fields is not None
    assert fields["context_window"] == 4096
    assert fields["max_tokens"] == 4096


def test_cost_tiers_merge_and_inherit_sparse_base_rates():
    entry = raw_model(
        cost={
            "text": {
                "input": {
                    "price": 10,
                    "cache": {
                        "read": 2,
                        "explicit": {"write": 4},
                    },
                    "overrides": [
                        {
                            "when": {"context": {"gt": 1000}},
                            "price": 8,
                            "cache": {"read": 1},
                        }
                    ],
                },
                "output": {
                    "price": 20,
                    "overrides": [{"when": {"context": {"gte": 1000}}, "price": 15}],
                },
            }
        }
    )
    fields = models_dev_to_nova("test", entry)
    assert fields is not None
    cost = fields["cost"]
    assert {
        key: cost[key] for key in ("input", "output", "cache_read", "cache_write")
    } == {
        "input": 10,
        "output": 20,
        "cache_read": 2,
        "cache_write": 4,
    }
    assert cost["tiers"] == [
        {
            "inputTokensAbove": 1000.0,
            "input": 8,
            "output": 15,
            "cache_read": 1,
            "cache_write": 4,
        }
    ]


def test_ttl_price_uses_first_value_and_non_token_cost_is_zero():
    ttl = models_dev_to_nova(
        "test",
        raw_model(
            cost={
                "text": {
                    "input": {
                        "price": 1,
                        "cache": {"write": {"5m": 3, "1h": 6}},
                    },
                    "output": {"price": 2},
                }
            }
        ),
    )
    assert ttl is not None and ttl["cost"]["cache_write"] == 3
    non_token = models_dev_to_nova(
        "test",
        raw_model(
            cost={
                "text": {
                    "input": {"unit": "request", "price": 1},
                    "output": {"price": 2},
                }
            }
        ),
    )
    assert non_token is not None
    assert non_token["cost"] == {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
    }


@pytest.mark.asyncio
@respx.mock
async def test_http_single_get_json():
    url = "https://models.dev/experimental/v2.0/api.json"
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"ok": True}))
    result = await fetch_json(url)
    assert result == {"ok": True}
    assert route.call_count == 1
    assert route.calls[0].request.headers["accept"] == "application/json"


@pytest.mark.asyncio
@respx.mock
async def test_http_non_200_fails_without_retry():
    url = "https://models.dev/experimental/v2.0/api.json"
    route = respx.get(url).mock(return_value=httpx.Response(500))
    with pytest.raises(FetchError, match="returned 500"):
        await fetch_json(url)
    assert route.call_count == 1
