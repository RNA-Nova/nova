"""模型与用量词汇（model.py）测试——Model/Cost/Usage/ModelCost。"""

import pytest
from nova_protocol import (
    Cost,
    ImageModel,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    ModelImageInputLimits,
    ModelImageResizeOptions,
    ModelInputLimits,
    ModelPromptCache,
    ModelsStoreEntry,
    OpenAICompletionsCompat,
    Usage,
)


class TestUsageAndCost:
    """用量和成本。"""

    def test_cost_default(self):
        c = Cost()
        assert c.input == 0.0
        assert c.output == 0.0
        assert c.cache_read == 0.0
        assert c.cache_write == 0.0
        assert c.total == 0.0

    def test_usage_default(self):
        u = Usage()
        assert u.input == 0
        assert u.output == 0
        assert u.cache_read == 0
        assert u.cache_write == 0
        assert u.total_tokens == 0
        assert isinstance(u.cost, Cost)

    def test_usage_with_cost(self):
        u = Usage(input=10, output=20, cost=Cost(input=0.01, output=0.02, total=0.03))
        assert u.input == 10
        assert u.cost.total == 0.03


def _model_dict(**overrides):
    base = {
        "id": "test-model",
        "name": "Test Model",
        "api": KnownApi.OPENAI_COMPLETIONS,
        "provider": KnownProvider.OPENAI,
        "base_url": "https://api.openai.com",
        "reasoning": False,
        "input_types": ["text"],
        "cost": ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
        "context_window": 128000,
        "max_tokens": 4096,
    }
    return base | overrides


class TestModel:
    def test_model_creation(self):
        m = Model(**_model_dict())
        assert m.id == "test-model"
        assert m.reasoning is False
        assert m.thinking_level_map is None

    def test_model_with_thinking_level_map(self):
        m = Model(
            **_model_dict(
                reasoning=True, thinking_level_map={"high": "high", "xhigh": "max"}
            )
        )
        assert m.thinking_level_map == {"high": "high", "xhigh": "max"}

    def test_model_auto_compat(self):
        """未显式给 compat 时按 api 自动补默认实例。"""
        m = Model(**_model_dict())
        assert m.compat is not None
        assert isinstance(m.compat, OpenAICompletionsCompat)


class TestImageModel:
    def test_mixed_store_roundtrip_preserves_type_with_same_id(self):
        chat = Model(**_model_dict())
        image = ImageModel(
            id=chat.id,
            name="Test Image Model",
            type="image",
            api="openrouter-images",
            provider=chat.provider,
            base_url="https://openrouter.ai/api/v1",
            input_types=["text", "image"],
            output_types=["text", "image"],
            cost=ModelCost(input=1, output=2, cache_read=0, cache_write=0),
        )
        entry = ModelsStoreEntry(models=[chat, image])
        restored = ModelsStoreEntry.model_validate(entry.model_dump())
        assert restored == entry
        assert isinstance(restored.models[0], Model)
        assert isinstance(restored.models[1], ImageModel)

    def test_legacy_chat_without_type_is_accepted(self):
        from nova_protocol import AnyModel
        from pydantic import TypeAdapter

        entry = ModelsStoreEntry.model_validate({"models": [_model_dict()]})
        assert entry.models[0].type == "chat"
        direct = TypeAdapter(AnyModel).validate_python(_model_dict())
        assert isinstance(direct, Model)
        assert direct.type == "chat"

    def test_image_output_is_required(self):
        with pytest.raises(ValueError, match="output_types"):
            ImageModel(
                id="image-only",
                name="Image Only",
                type="image",
                api="openrouter-images",
                provider="openrouter",
                base_url="https://openrouter.ai/api/v1",
                input_types=["text"],
                output_types=["text"],
                cost=ModelCost(),
            )


def test_model_limits_and_prompt_cache_roundtrip_from_public_surface():
    from nova_protocol import (
        ModelImageInputLimits,
        ModelImageResizeOptions,
        ModelInputLimits,
        ModelPromptCache,
    )

    model = Model(
        **_model_dict(
            input_types=["text", "image"],
            input_limits=ModelInputLimits(
                max_request_bytes=1024,
                images=ModelImageInputLimits(
                    max_per_message=2,
                    resize=ModelImageResizeOptions(max_width=1000, jpeg_quality=80),
                ),
            ),
            prompt_cache=ModelPromptCache(short=300, long=3600),
        )
    )
    restored = Model.model_validate(model.model_dump())
    assert restored == model
    assert restored.input_limits is not None
    assert restored.input_limits.images is not None
    assert restored.input_limits.images.resize is not None
    assert restored.input_limits.images.resize.max_width == 1000


def test_cache_write_1h_uses_pi_wire_spelling_in_nested_usage():
    from nova_protocol import AssistantMessage

    usage = Usage(cache_write_1h=7)
    assert usage.dump_wire()["cacheWrite1h"] == 7
    assert Usage.model_validate({"cacheWrite1h": 7}).cache_write_1h == 7
    message = AssistantMessage(usage=usage)
    assert message.dump_wire()["usage"]["cacheWrite1h"] == 7
    assert message.model_dump()["usage"]["cache_write_1h"] == 7
    validation_schema = Usage.model_json_schema(mode="validation")["properties"]
    serialization_schema = Usage.model_json_schema(mode="serialization")["properties"]
    assert "cacheWrite1h" in validation_schema
    assert "cache_write_1h" not in validation_schema
    assert "cacheWrite1h" in serialization_schema


@pytest.mark.parametrize(
    "factory",
    [
        lambda: ModelImageResizeOptions(max_width=0),
        lambda: ModelImageResizeOptions(max_height=-1),
        lambda: ModelImageResizeOptions(max_bytes=0),
        lambda: ModelImageResizeOptions(jpeg_quality=0),
        lambda: ModelImageResizeOptions(jpeg_quality=101),
        lambda: ModelImageInputLimits(max_per_message=0),
        lambda: ModelImageInputLimits(max_per_request=-1),
        lambda: ModelInputLimits(max_request_bytes=0),
        lambda: ModelPromptCache(short=0),
        lambda: ModelPromptCache(long=-1),
    ],
)
def test_model_limits_reject_out_of_range_values(factory):
    with pytest.raises(ValueError):
        factory()


def test_store_drops_only_unknown_model_types():
    unknown = {**_model_dict(), "id": "future", "type": "future-operation"}
    entry = ModelsStoreEntry.model_validate(
        {"models": [_model_dict(), unknown], "checked_at": 123}
    )
    assert [model.id for model in entry.models] == ["test-model"]
    assert entry.checked_at == 123

    malformed_known = {**_model_dict(), "type": "chat", "context_window": "bad"}
    with pytest.raises(ValueError):
        ModelsStoreEntry.model_validate({"models": [malformed_known]})


def test_classifier_model_participates_in_mixed_store_roundtrip():
    from nova_protocol import ClassifierModel, SystemOneCompat

    classifier = ClassifierModel(
        id="shared",
        name="Decision",
        type="classifier",
        api="typesafe-system-one",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input_types=["text", "image"],
        cost=ModelCost(input=0.042, output=0, cache_read=0, cache_write=0),
        context_window=64000,
        compat=SystemOneCompat(
            image_input="state-image-urls", send_image_opt_in_header=False
        ),
    )
    entry = ModelsStoreEntry(models=[Model(**_model_dict(id="shared")), classifier])
    restored = ModelsStoreEntry.model_validate(entry.model_dump())
    assert restored == entry
    assert isinstance(restored.models[1], ClassifierModel)
    assert restored.models[1].compat == classifier.compat
