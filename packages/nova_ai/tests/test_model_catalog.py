"""模型目录运行时测试：身份盖戳、拍平与生成时间锚点。"""

import pytest
from nova_ai.model_catalog import (
    builtin_catalog_generated_at,
    finalize_catalog_models,
    flatten_chat_model_catalog,
    flatten_image_model_catalog,
)
from nova_protocol import ModelFields


def _fields(model_id, **overrides):
    fields = {
        "id": model_id,
        "name": model_id,
        "base_url": "http://fields/v1",
        "api": "openai-completions",
        "reasoning": False,
        "input_types": ["text"],
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        "context_window": 128000,
        "max_tokens": 8192,
    }
    fields.update(overrides)
    return ModelFields(**fields)


class TestApiResolution:
    def test_source_field_wins(self):
        fields = _fields("m1", api="anthropic-messages")
        models = finalize_catalog_models("p1", "http://x/v1", [fields])
        assert models[0].api == "anthropic-messages"

    def test_raise_when_api_is_absent(self):
        fields = _fields("m1")
        del fields["api"]
        with pytest.raises(ValueError, match="Cannot determine catalog API"):
            finalize_catalog_models("p1", "http://x/v1", [fields])


class TestFinalizeCatalogModels:
    def test_stamps_identity_without_erasing_source_endpoint(self):
        fields = _fields(
            "m1",
            base_url="http://source/v1",
            api="anthropic-messages",
            provider="other",
        )
        models = finalize_catalog_models("p1", "http://stamp/v1", [fields])
        assert len(models) == 1
        model = models[0]
        assert model.provider == "p1"
        assert model.base_url == "http://source/v1"
        assert model.api == "anthropic-messages"

    def test_dedup_by_id_last_wins(self):
        older = _fields("m1", name="old")
        newer = _fields("m1", name="new")
        models = finalize_catalog_models("p1", "http://x/v1", [older, newer])
        assert [m.name for m in models] == ["new"]

    def test_entries_without_id_skipped(self):
        fields = _fields("m1")
        del fields["id"]
        assert finalize_catalog_models("p1", "http://x/v1", [fields]) == []

    def test_mixed_entries_keep_same_id_and_drop_unknown_types(self):
        image = ModelFields(
            id="m1",
            type="image",
            name="image",
            api="openrouter-images",
            input_types=["text"],
            output_types=["image"],
            cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        )
        unknown = _fields("future", type="future-operation")
        models = finalize_catalog_models(
            "p1", "http://x/v1", [_fields("m1"), image, unknown]
        )
        assert [(model.type, model.id) for model in models] == [
            ("chat", "m1"),
            ("image", "m1"),
        ]
        assert models[0].provider == "p1"
        assert models[1].base_url == "http://x/v1"

    @pytest.mark.parametrize("invalid_type", ["", None, False, "future"])
    def test_remote_explicit_invalid_types_are_not_legacy_chat(self, invalid_type):
        fields = _fields("m1", type=invalid_type)
        assert finalize_catalog_models("p1", "http://x/v1", [fields]) == []

    def test_dynamic_image_materializes_without_chat_knowledge(self):
        image = ModelFields(
            id="image",
            type="image",
            name="image",
            api="openrouter-images",
            input_types=["text", "image"],
            output_types=["image"],
            cost={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        )
        models = finalize_catalog_models("test", "https://proxy.example/v1", [image])
        assert models[0].input_types == ["text", "image"]
        assert models[0].base_url == "https://proxy.example/v1"

    def test_dynamic_classifier_materializes_without_chat_fields(self):
        classifier = ModelFields(
            id="decision",
            type="classifier",
            name="Decision",
            api="typesafe-system-one",
            input_types=["text", "image"],
            cost={"input": 0.042, "output": 0, "cache_read": 0, "cache_write": 0},
            context_window=32000,
        )
        models = finalize_catalog_models(
            "openrouter", "https://openrouter.ai/api/v1", [classifier]
        )
        assert models[0].type == "classifier"
        assert models[0].context_window == 32000
        assert models[0].api == "typesafe-system-one"
        # 与静态构建及 pi 一致：分类图片不额外补默认 resize。
        assert models[0].input_limits is None


class TestFlattenModelCatalog:
    def test_happy_path_stamps_api_and_provider(self):
        groups = {"openai-completions": {"chat:m1": _fields("m1")}}
        models = flatten_chat_model_catalog("p1", groups)
        assert models["m1"].api == "openai-completions"
        assert models["m1"].provider == "p1"

    def test_custom_api_group_is_preserved(self):
        models = flatten_chat_model_catalog(
            "p1", {"custom-api": {"chat:m1": _fields("m1", api="custom-api")}}
        )
        assert models["m1"].api == "custom-api"

    def test_id_mismatch_raises(self):
        fields = _fields("other-id")
        with pytest.raises(ValueError, match="data id mismatch"):
            flatten_chat_model_catalog(
                "p1", {"openai-completions": {"chat:m1": fields}}
            )

    def test_api_mismatch_raises(self):
        fields = _fields("m1", api="anthropic-messages")
        with pytest.raises(ValueError, match="data api"):
            flatten_chat_model_catalog(
                "p1", {"openai-completions": {"chat:m1": fields}}
            )

    def test_chat_and_image_entries_split_with_same_id(self):
        image = {
            "id": "m1",
            "type": "image",
            "name": "m1",
            "api": "openrouter-images",
            "provider": "p1",
            "base_url": "http://x/v1",
            "input_types": ["text"],
            "output_types": ["image"],
            "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        }
        groups = {
            "openai-completions": {"chat:m1": _fields("m1")},
            "openrouter-images": {"image:m1": image},
        }
        chat = flatten_chat_model_catalog("p1", groups)
        images = flatten_image_model_catalog("p1", groups)
        assert chat["m1"].type == "chat"
        assert images["m1"].type == "image"
        assert images["m1"].output_types == ["image"]


class TestBuiltinCatalogGeneratedAt:
    def test_real_manifest_returns_epoch_ms(self):
        generated_at = builtin_catalog_generated_at()
        assert isinstance(generated_at, int)
        assert generated_at > 0

    def test_missing_manifest_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr("importlib.resources.files", lambda anchor: tmp_path)
        assert builtin_catalog_generated_at() is None
