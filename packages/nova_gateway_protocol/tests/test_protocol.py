"""公开请求、版本与 Schema 的契约测试。"""

import json

import pytest
from nova_protocol import Context, Model, ModelCost, UserMessage
from pydantic import ValidationError

from nova_gateway_protocol import (
    ModelListItem,
    ModelRef,
    StreamOptions,
    StreamRequest,
    protocol_schema,
)


def _model() -> Model:
    return Model(
        id="model-a",
        name="Model A",
        api="openai-completions",
        provider="test-provider",
        base_url="https://example.com/v1",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=1, output=2, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


def test_stream_request_roundtrip_uses_model_ref_not_full_model():
    request = StreamRequest(
        model=ModelRef(provider="test-provider", id="model-a"),
        context=Context(messages=[UserMessage(content="hi")]),
        options=StreamOptions(temperature=0.2, max_tokens=128),
    )
    payload = request.dump_wire()
    assert payload["model"] == {"provider": "test-provider", "id": "model-a"}
    assert "baseUrl" not in payload["model"]
    restored = StreamRequest.model_validate(payload)
    assert restored == request
    assert restored.options.temperature == 0.2
    assert restored.options.max_tokens == 128


def test_stream_request_rejects_unknown_model_shape():
    with pytest.raises(ValidationError):
        StreamRequest.model_validate(
            {
                "model": {
                    "provider": "test-provider",
                    "id": "model-a",
                    "baseUrl": "https://attacker.example",
                },
                "context": {"messages": []},
            }
        )


def test_model_list_item_is_built_from_runtime_model():
    item = ModelListItem.from_model(_model())
    assert item.provider == "test-provider"
    assert item.id == "model-a"
    assert item.reasoning is True
    assert item.context_window == 128000
    assert item.max_tokens == 8192


def test_protocol_schema_is_derived_from_dto():
    schema = protocol_schema()
    assert schema["protocol"] == {
        "major": 1,
        "minor": 2,
        "header": "x-nova-gateway-protocol",
    }
    assert schema["streamRequest"]["type"] == "object"
    event_schema = json.loads(json.dumps(schema["assistantMessageEvent"]))
    assert "oneOf" in event_schema or "anyOf" in event_schema
    assert "discriminator" in event_schema
