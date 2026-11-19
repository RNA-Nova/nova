"""火山 models.dev 原料修正、pinned 事实与淘汰集契约。"""

from nova_protocol import ModelFields

from nova_catalog.providers.volcengine import materialize_volcengine_fields


def test_materialize_applies_only_evidence_based_volcengine_corrections():
    fields = [
        ModelFields(
            id="deepseek-v4-flash-test",
            name="DeepSeek",
            reasoning=True,
            input_types=["text"],
            cost={"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
            context_window=1000000,
            max_tokens=1000000,
        ),
        ModelFields(
            id="doubao-seed-character-test",
            name="Character",
            reasoning=True,
            input_types=["text", "image"],
            cost={"input": 1, "output": 2, "cache_read": 0, "cache_write": 0},
            context_window=256000,
            max_tokens=256000,
        ),
    ]
    result = materialize_volcengine_fields(fields, {})
    deepseek, character = result.models_fields
    assert deepseek.get("provider") == "volcengine"
    assert deepseek.get("api") == "openai-completions"
    levels = deepseek.get("thinking_level_map")
    assert levels is not None
    assert levels.get("off") == "none"
    assert levels.get("xhigh") == "xhigh"
    compat = deepseek.get("compat")
    assert compat is not None
    assert compat.get("thinking_format") == "deepseek"
    assert character.get("max_tokens") == 32768

    filtered = materialize_volcengine_fields(
        fields, {"deepseek-v4-flash-test": "deprecated"}
    )
    assert [field.get("id") for field in filtered.models_fields] == [
        "doubao-seed-character-test"
    ]
    assert filtered.remove_ids == frozenset({"deepseek-v4-flash-test"})


def test_materialize_preserves_complete_pinned_facts_over_upstream():
    pinned_id = "deepseek-v4-flash-260425"
    upstream = ModelFields(
        id=pinned_id,
        name="Upstream",
        reasoning=False,
        input_types=["text"],
        cost={"input": 99, "output": 99, "cache_read": 99, "cache_write": 99},
        context_window=4096,
        max_tokens=4096,
    )
    result = materialize_volcengine_fields([upstream], {})
    model = result.models_fields[0]
    assert model.get("name") == "Deepseek-V4-Flash"
    cost = model.get("cost")
    assert cost is not None
    assert cost.get("input") == 1
    assert model.get("context_window") == 1048576
    assert model.get("max_tokens") == 393216
