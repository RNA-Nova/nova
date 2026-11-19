"""models.dev 源翻译、成本归一与 strict 边界测试。"""

import pytest
from nova_protocol import Model

from nova_catalog.define import stamp_fields
from nova_catalog.sources.models_dev import models_dev_to_nova
from tests._helpers import raw_model


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


@pytest.mark.parametrize(
    "cost",
    [
        {"text": []},
        {"text": {"input": [], "output": {"price": 2}}},
        {"text": {"input": {"price": "1"}, "output": {"price": 2}}},
        {
            "text": {
                "input": {"price": 1, "cache": {"read": "0"}},
                "output": {"price": 2},
            }
        },
        {
            "text": {
                "input": {"price": 1, "overrides": {}},
                "output": {"price": 2},
            }
        },
    ],
)
def test_strict_rejects_malformed_cost_descendants(cost):
    entry = raw_model(cost=cost)
    with pytest.raises(ValueError, match="cost"):
        models_dev_to_nova("test", entry, strict=True)


@pytest.mark.parametrize(
    "limit",
    [
        {"context": "128000", "output": 8192},
        {"context": 128000, "output": True},
        {"context": 0, "output": 8192},
    ],
)
def test_strict_rejects_invalid_limit_values_instead_of_defaulting(limit):
    entry = raw_model(limit=limit)
    with pytest.raises(ValueError, match="limit"):
        models_dev_to_nova("test", entry, strict=True)


@pytest.mark.parametrize(
    "capabilities",
    [
        {"tools": {"supported": "yes"}},
        {"tools": {"supported": True}, "reasoning": {"supported": "yes"}},
        {
            "tools": {"supported": True},
            "reasoning": {"supported": True, "effort": {}},
        },
        {
            "tools": {"supported": True},
            "reasoning": {"supported": True, "toggle": "yes"},
        },
    ],
)
def test_strict_rejects_malformed_capability_values(capabilities):
    entry = raw_model(capabilities=capabilities)
    with pytest.raises(ValueError, match="capabilities"):
        models_dev_to_nova("test", entry, strict=True)


@pytest.mark.parametrize(
    "path",
    [
        ("capabilities",),
        ("limit",),
        ("cost",),
        ("capabilities", "tools"),
        ("capabilities", "reasoning"),
    ],
)
def test_strict_rejects_explicit_null_instead_of_treating_it_as_absent(path):
    entry = raw_model()
    target = entry
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = None
    with pytest.raises(ValueError, match=path[-1]):
        models_dev_to_nova("test", entry, strict=True)


def test_limit_array_falls_back_to_default_windows():
    entry = raw_model()
    entry["limit"] = [1]
    fields = models_dev_to_nova("test", entry)
    assert fields is not None
    assert fields.get("context_window") == 4096
    assert fields.get("max_tokens") == 4096


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
    cost = fields.get("cost")
    assert cost is not None
    assert {
        key: cost.get(key) for key in ("input", "output", "cache_read", "cache_write")
    } == {
        "input": 10,
        "output": 20,
        "cache_read": 2,
        "cache_write": 4,
    }
    # gt:1000 是 >1000；gte:1000 则是 >=1000，内部独占阈值应为 999。
    assert cost.get("tiers") == [
        {
            "inputTokensAbove": 999,
            "input": 10,
            "output": 15,
            "cache_read": 2,
            "cache_write": 4,
        },
        {
            "inputTokensAbove": 1000.0,
            "input": 8,
            "output": 20,
            "cache_read": 1,
            "cache_write": 4,
        },
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
    assert ttl is not None
    ttl_cost = ttl.get("cost")
    assert ttl_cost is not None
    assert ttl_cost.get("cache_write") == 3
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
    assert non_token.get("cost") == {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
    }


def test_non_chat_type_is_dropped():
    assert models_dev_to_nova("test", raw_model(type="image")) is None


def test_deprecated_is_kept_by_default_and_dropped_when_opted():
    entry = raw_model(status="deprecated")
    assert models_dev_to_nova("test", entry) is not None
    assert models_dev_to_nova("test", entry, skip_deprecated=True) is None


def test_tools_unsupported_is_dropped():
    entry = raw_model(capabilities={"tools": {"supported": False}})
    assert models_dev_to_nova("test", entry) is None


def test_missing_name_falls_back_to_id():
    entry = raw_model()
    del entry["name"]
    fields = models_dev_to_nova("test", entry)
    assert fields is not None
    assert fields.get("name") == entry["id"]


def test_strict_raises_on_missing_id():
    entry = raw_model()
    del entry["id"]
    with pytest.raises(ValueError, match="missing id"):
        models_dev_to_nova("test", entry, strict=True)


def test_strict_rejects_present_but_invalid_name():
    with pytest.raises(ValueError, match="name"):
        models_dev_to_nova("test", raw_model(name=42), strict=True)


def test_modalities_are_filtered_to_text_and_image():
    fields = models_dev_to_nova(
        "test", raw_model(modalities={"input": ["text", "image", "audio"]})
    )
    assert fields is not None
    assert fields.get("input_types") == ["text", "image"]
    only_audio = models_dev_to_nova("test", raw_model(modalities={"input": ["audio"]}))
    assert only_audio is not None
    assert only_audio.get("input_types") == ["text"]


def test_fractional_gt_threshold_has_exact_integer_token_semantics():
    entry = raw_model(
        cost={
            "text": {
                "input": {
                    "price": 1,
                    "unit": "token",
                    "overrides": [{"when": {"context": {"gt": 1000.5}}, "price": 2}],
                },
                "output": {"price": 2, "unit": "token"},
            }
        }
    )
    fields = models_dev_to_nova("test", entry, strict=True)
    assert fields is not None
    cost = fields.get("cost")
    assert cost is not None
    tiers = cost.get("tiers")
    assert tiers is not None
    threshold = tiers[0].get("inputTokensAbove")
    assert threshold == 1000
    assert isinstance(threshold, int)
    stamped = stamp_fields(
        "model-a",
        fields,
        provider_id="test",
        base_url="https://example.com/v1",
        api="openai-completions",
    )
    Model.model_validate(stamped, strict=True)
