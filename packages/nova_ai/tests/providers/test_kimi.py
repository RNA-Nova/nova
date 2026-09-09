"""Kimi / Moonshot AI 系列 provider 测试。

对齐 TS 设计：拆分为 moonshotai、moonshotai-cn、kimi-coding 三个 provider。
"""

from nova_ai import Context, UserMessage
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
    detect_compat,
)
from nova_ai.providers.kimi_coding import (
    KIMI_CODING_MODELS,
    get_kimi_coding_model,
    kimi_coding_provider,
)
from nova_ai.providers.moonshotai import (
    MOONSHOTAI_MODELS,
    get_moonshotai_model,
    moonshotai_provider,
)
from nova_ai.providers.moonshotai_cn import (
    MOONSHOTAI_CN_MODELS,
    get_moonshotai_cn_model,
    moonshotai_cn_provider,
)
from nova_ai.types import KnownApi, KnownProvider, ThinkingFormat


class TestMoonshotaiProvider:
    def test_provider_id_and_name(self):
        provider = moonshotai_provider()
        assert provider.id == "moonshotai"
        assert provider.name == "Moonshot AI"

    def test_provider_api_impl(self):
        provider = moonshotai_provider()
        assert provider.api_impl is not None

    def test_provider_auth_is_api_key(self):
        provider = moonshotai_provider()
        assert provider.auth is not None
        assert provider.auth.api_key is not None
        assert provider.auth.oauth is None

    def test_models_registered(self):
        provider = moonshotai_provider()
        model_ids = {m.id for m in provider.get_models()}
        assert model_ids == set(MOONSHOTAI_MODELS.keys())


class TestMoonshotaiCnProvider:
    def test_provider_id_and_name(self):
        provider = moonshotai_cn_provider()
        assert provider.id == "moonshotai-cn"
        assert provider.name == "Moonshot AI CN"

    def test_provider_api_impl(self):
        provider = moonshotai_cn_provider()
        assert provider.api_impl is not None

    def test_provider_auth_is_api_key(self):
        provider = moonshotai_cn_provider()
        assert provider.auth is not None
        assert provider.auth.api_key is not None
        assert provider.auth.oauth is None

    def test_models_registered(self):
        provider = moonshotai_cn_provider()
        model_ids = {m.id for m in provider.get_models()}
        assert model_ids == set(MOONSHOTAI_CN_MODELS.keys())


class TestKimiCodingProvider:
    def test_provider_id_and_name(self):
        provider = kimi_coding_provider()
        assert provider.id == "kimi-coding"
        assert provider.name == "Kimi Coding"

    def test_provider_api_impl(self):
        provider = kimi_coding_provider()
        assert provider.api_impl is not None

    def test_provider_auth_has_both_api_key_and_oauth(self):
        provider = kimi_coding_provider()
        assert provider.auth is not None
        assert provider.auth.api_key is not None
        assert provider.auth.oauth is not None
        assert provider.auth.oauth.name == "Kimi (Moonshot AI)"

    def test_models_registered(self):
        provider = kimi_coding_provider()
        model_ids = {m.id for m in provider.get_models()}
        assert model_ids == set(KIMI_CODING_MODELS.keys())


class TestMoonshotaiModels:
    def test_kimi_k3_model_attributes(self):
        model = get_moonshotai_model("kimi-k3")
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.MOONSHOTAI
        assert model.base_url == "https://api.moonshot.ai/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_kimi_k3_thinking_level_map(self):
        model = get_moonshotai_model("kimi-k3")
        # k3 当前支持 low/high/max
        assert model.thinking_level_map["max"] == "max"
        assert model.thinking_level_map["low"] == "low"
        assert model.thinking_level_map["high"] == "high"
        assert model.thinking_level_map["minimal"] is None
        assert model.thinking_level_map["medium"] is None
        assert model.thinking_level_map["off"] is None

    def test_compat_detected_as_openai_format(self):
        model = get_moonshotai_model("kimi-k3")
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI


class TestMoonshotaiCnModels:
    def test_kimi_k3_model_attributes(self):
        model = get_moonshotai_cn_model("kimi-k3")
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.MOONSHOTAI_CN
        assert model.base_url == "https://api.moonshot.cn/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_kimi_k3_thinking_level_map(self):
        model = get_moonshotai_cn_model("kimi-k3")
        # k3 当前支持 low/high/max
        assert model.thinking_level_map["max"] == "max"
        assert model.thinking_level_map["low"] == "low"
        assert model.thinking_level_map["high"] == "high"
        assert model.thinking_level_map["minimal"] is None
        assert model.thinking_level_map["off"] is None

    def test_compat_is_openai_format(self):
        model = get_moonshotai_cn_model("kimi-k3")
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI


class TestKimiCodingModels:
    def test_k3_model_attributes(self):
        model = get_kimi_coding_model("k3")
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.KIMI_CODING
        assert model.base_url == "https://api.kimi.com/coding/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_k3_thinking_level_map(self):
        model = get_kimi_coding_model("k3")
        assert model.thinking_level_map["max"] == "max"
        assert model.thinking_level_map["high"] == "high"
        assert model.thinking_level_map["low"] == "low"
        assert model.thinking_level_map["off"] is None

    def test_kimi_for_coding_supports_thinking_toggle(self):
        model = get_kimi_coding_model("kimi-for-coding")
        assert model.thinking_level_map["off"] is None
        assert model.thinking_level_map["low"] == "low"
        assert model.thinking_level_map["max"] == "max"

    def test_compat_is_openai_format(self):
        model = get_kimi_coding_model("k3")
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI


class TestKimiBuildParams:
    def test_kimi_coding_k3_basic_params(self):
        model = get_kimi_coding_model("k3")
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert params["model"] == "k3"
        assert params["stream"] is True
        assert "messages" in params

    def test_kimi_coding_k3_reasoning_effort_max(self):
        model = get_kimi_coding_model("k3")
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="max")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "max"

    def test_moonshotai_kimi_k3_reasoning_uses_deepseek_thinking_format(self):
        # moonshot 目录的 k3 走 deepseek thinking 格式：
        # reasoning_effort 不直接发送，而是 extra_body.thinking.enabled
        model = get_moonshotai_cn_model("kimi-k3")
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="max")
        params = build_params(model, ctx, options)

        assert "reasoning_effort" not in params
        assert params["extra_body"]["thinking"] == {"type": "enabled"}

    def test_kimi_for_coding_reasoning_effort_mapped(self):
        model = get_kimi_coding_model("kimi-for-coding")
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="low")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "low"

    def test_reasoning_off_does_not_send_effort(self):
        model = get_kimi_coding_model("kimi-for-coding")
        ctx = Context(messages=[UserMessage(content="hello")])
        params = build_params(model, ctx)

        assert "reasoning_effort" not in params
