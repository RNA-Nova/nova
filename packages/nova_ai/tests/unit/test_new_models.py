"""新 provider 模型详细字段测试（moonshotai / moonshotai-cn / kimi-coding）。"""

import pytest

from nova_ai import create_models
from nova_ai.gateway import create_provider
from nova_ai.providers.kimi_coding import KIMI_CODING_MODELS, get_kimi_coding_model
from nova_ai.providers.moonshotai import MOONSHOTAI_MODELS, get_moonshotai_model
from nova_ai.providers.moonshotai_cn import (
    MOONSHOTAI_CN_MODELS,
    get_moonshotai_cn_model,
)
from nova_ai.types import (
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    UserMessage,
)


class TestMoonshotaiModels:
    def test_models_count(self):
        assert len(MOONSHOTAI_MODELS) == 10

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
        m = get_moonshotai_model("kimi-k3")
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131072
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["off"] is None

    def test_kimi_k2_5_fields(self):
        m = get_moonshotai_model("kimi-k2.5")
        assert m.id == "kimi-k2.5"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 262144
        assert m.max_tokens == 262144

    def test_kimi_k2_thinking_fields(self):
        m = get_moonshotai_model("kimi-k2-thinking")
        assert m.id == "kimi-k2-thinking"
        assert m.reasoning is True
        assert m.input_types == ["text"]

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_moonshotai_model("nonexistent")


class TestMoonshotaiCnModels:
    def test_models_count(self):
        assert len(MOONSHOTAI_CN_MODELS) == 10

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
        m = get_moonshotai_cn_model("kimi-k3")
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131_072
        # k3 当前支持 low/high/max
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["low"] == "low"
        assert m.thinking_level_map["high"] == "high"
        assert m.thinking_level_map["minimal"] is None
        assert m.thinking_level_map["medium"] is None
        assert m.thinking_level_map["off"] is None

    def test_kimi_k2_6_fields(self):
        m = get_moonshotai_cn_model("kimi-k2.6")
        assert m.id == "kimi-k2.6"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144

    def test_kimi_k2_7_code_fields(self):
        m = get_moonshotai_cn_model("kimi-k2.7-code")
        assert m.id == "kimi-k2.7-code"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_moonshotai_cn_model("nonexistent")


class TestKimiCodingModels:
    def test_models_count(self):
        assert len(KIMI_CODING_MODELS) == 5

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
        m = get_kimi_coding_model("k3")
        assert m.id == "k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131_072
        # k3 当前支持 low/high/max
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["low"] == "low"
        assert m.thinking_level_map["high"] == "high"
        assert m.thinking_level_map["minimal"] is None
        assert m.thinking_level_map["medium"] is None
        assert m.thinking_level_map["off"] is None

    def test_kimi_for_coding_fields(self):
        m = get_kimi_coding_model("kimi-for-coding")
        assert m.id == "kimi-for-coding"
        assert m.name == "Kimi For Coding"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 32768

    def test_kimi_for_coding_highspeed_fields(self):
        m = get_kimi_coding_model("kimi-for-coding-highspeed")
        assert m.id == "kimi-for-coding-highspeed"
        assert m.name == "Kimi For Coding HighSpeed"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 32768

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_kimi_coding_model("nonexistent")


class TestStreamContract:
    """stream 契约收口（lazy_stream 单点强制）：失败一律编码进流。"""

    @pytest.mark.asyncio
    async def test_unknown_provider_yields_error_stream(self):
        """未知 provider 不再同步抛——返回以 error 事件结束的流。"""
        from nova_ai.types import DoneEvent, ErrorEvent

        models = create_models()
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

    @pytest.mark.asyncio
    async def test_unconfigured_provider_yields_error_stream(self):
        """provider 存在但未配置 auth：同样 error 流，不抛异常。"""
        from nova_ai.types import ApiKeyAuth, ProviderAuth

        async def _resolve(_ctx):
            return None

        models = create_models()
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
