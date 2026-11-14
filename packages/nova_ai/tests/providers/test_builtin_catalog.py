"""新 provider 模型详细字段测试（moonshotai / moonshotai-cn / kimi-coding）。"""

import pytest
from nova_ai import create_models_manager
from nova_ai.gateway import create_provider
from nova_ai.providers.kimi_coding import KIMI_CODING_MODELS
from nova_ai.providers.moonshotai import MOONSHOTAI_MODELS
from nova_ai.providers.moonshotai_cn import MOONSHOTAI_CN_MODELS
from nova_protocol import (
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    NovaErrorKind,
    UserMessage,
)


def _level(model: Model, key: str):
    levels = model.thinking_level_map
    assert levels is not None
    return levels.get(key)


class TestMoonshotaiModels:
    def test_models_exist(self):
        assert MOONSHOTAI_MODELS

    def test_all_models_have_required_fields(self):
        for model_id, model in MOONSHOTAI_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.MOONSHOTAI
            assert model.base_url == "https://api.moonshot.ai/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0
            assert model.cost is not None

    def test_kimi_k3_fields(self):
        m = MOONSHOTAI_MODELS["kimi-k3"]
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 1048576
        assert _level(m, "max") == "max"
        assert _level(m, "off") is None

    def test_kimi_k2_6_fields(self):
        m = MOONSHOTAI_MODELS["kimi-k2.6"]
        assert m.id == "kimi-k2.6"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 262144
        assert m.max_tokens == 262144


class TestMoonshotaiCnModels:
    def test_models_exist(self):
        assert MOONSHOTAI_CN_MODELS

    def test_all_models_have_required_fields(self):
        for model_id, model in MOONSHOTAI_CN_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.MOONSHOTAI_CN
            assert model.base_url == "https://api.moonshot.cn/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0

    def test_kimi_k3_fields(self):
        m = MOONSHOTAI_CN_MODELS["kimi-k3"]
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.context_window == 1_048_576
        assert m.max_tokens == 1_048_576
        # k3 当前支持 low/high/max
        assert _level(m, "max") == "max"
        assert _level(m, "low") == "low"
        assert _level(m, "high") == "high"
        assert _level(m, "minimal") is None
        assert _level(m, "medium") is None
        assert _level(m, "off") is None

    def test_kimi_k2_6_fields(self):
        m = MOONSHOTAI_CN_MODELS["kimi-k2.6"]
        assert m.id == "kimi-k2.6"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144

    def test_kimi_k2_7_code_fields(self):
        m = MOONSHOTAI_CN_MODELS["kimi-k2.7-code"]
        assert m.id == "kimi-k2.7-code"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144


class TestKimiCodingModels:
    def test_models_exist(self):
        assert KIMI_CODING_MODELS

    def test_all_models_have_required_fields(self):
        for model_id, model in KIMI_CODING_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.KIMI_CODING
            assert model.base_url == "https://api.kimi.com/coding/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0

    def test_k3_fields(self):
        m = KIMI_CODING_MODELS["k3"]
        assert m.id == "k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131_072
        # k3 当前支持 low/high/max
        assert _level(m, "max") == "max"
        assert _level(m, "low") == "low"
        assert _level(m, "high") == "high"
        assert _level(m, "minimal") is None
        assert _level(m, "medium") is None
        assert _level(m, "off") is None

    def test_kimi_for_coding_fields(self):
        m = KIMI_CODING_MODELS["kimi-for-coding"]
        assert m.id == "kimi-for-coding"
        # name 随源事实：pi.dev / models.dev global 键均给裸 id（仅 cn 键有美化名）
        assert m.name == "kimi-for-coding"
        assert m.reasoning is True
        assert m.context_window == 1_048_576
        assert m.max_tokens == 32768

    def test_kimi_for_coding_highspeed_fields(self):
        m = KIMI_CODING_MODELS["kimi-for-coding-highspeed"]
        assert m.id == "kimi-for-coding-highspeed"
        assert m.name == "Kimi For Coding HighSpeed"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 32768


class TestStreamContract:
    """stream 契约收口（lazy_stream 单点强制）：失败一律编码进流。"""

    @pytest.mark.asyncio
    async def test_unknown_provider_yields_error_stream(self):
        """未知 provider 不再同步抛——返回以 error 事件结束的流。"""
        from nova_protocol import DoneEvent, ErrorEvent

        models = create_models_manager()
        model = Model(
            id="m1",
            name="M",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="nonexistent",
            base_url="https://example.com/v1",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        stream = models.stream(model, Context(messages=[UserMessage(content="hi")]))
        events = []
        async for event in stream:
            events.append(event)
        types = [e.type for e in events]
        assert "error" in types
        assert "done" not in types
        assert isinstance(events[-1], (ErrorEvent, DoneEvent)) or types[-1] == "error"
        result = await stream.result()
        assert result.stop_reason.value == "error"
        assert "Unknown provider" in (result.error_message or "")
        # 装配失败经归一单点分类上消息（无结构信息 → INTERNAL）
        assert result.error_kind is NovaErrorKind.INTERNAL

    @pytest.mark.asyncio
    async def test_unconfigured_provider_yields_error_stream(self):
        """provider 存在但未配置 auth：同样 error 流，不抛异常。"""
        from nova_ai.auth import ApiKeyAuth, ProviderAuth

        async def _resolve(_ctx):
            return None

        models = create_models_manager()
        models.set_provider(
            create_provider(
                id="p1",
                name="P",
                auth=ProviderAuth(api_key=ApiKeyAuth(name="k", resolve=_resolve)),
            )
        )
        model = Model(
            id="m1",
            name="M",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="p1",
            base_url="https://example.com/v1",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        stream = models.stream(model, Context(messages=[UserMessage(content="hi")]))
        types = [e.type async for e in stream]
        assert types == ["error"]


def test_openrouter_classifier_catalog_is_separate_from_chat():
    from nova_ai import (
        builtin_models,
        get_builtin_classifier_model,
        get_builtin_classifier_models,
    )

    classifiers = get_builtin_classifier_models("openrouter")
    assert classifiers
    for model in classifiers:
        assert model.type == "classifier"
        assert model.api == "typesafe-system-one"
        assert model.base_url == "https://openrouter.ai/api/v1"
        assert model.compat is not None
        assert model.compat.image_input == "state-image-urls"
        assert model.compat.send_image_opt_in_header is False
        assert get_builtin_classifier_model("openrouter", model.id) == model

    models = builtin_models()
    assert models.get_model("openrouter", classifiers[0].id) is None
    assert (
        models.get_model_of_type("classifier", "openrouter", classifiers[0].id)
        == classifiers[0]
    )
