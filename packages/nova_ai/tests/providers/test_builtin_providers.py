"""Kimi / Moonshot AI 系列 provider 测试。

对齐 TS 设计：拆分为 moonshotai、moonshotai-cn、kimi-coding 三个 provider。
"""

import pytest
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    build_params,
    detect_compat,
)
from nova_ai.auth import ApiKeyAuthInput
from nova_ai.providers.anthropic import anthropic_provider
from nova_ai.providers.kimi_coding import KIMI_CODING_MODELS, kimi_coding_provider
from nova_ai.providers.moonshotai import MOONSHOTAI_MODELS, moonshotai_provider
from nova_ai.providers.moonshotai_cn import MOONSHOTAI_CN_MODELS, moonshotai_cn_provider
from nova_protocol import (
    Context,
    KnownApi,
    KnownProvider,
    Model,
    ThinkingFormat,
    ThinkingLevelMap,
    UserMessage,
)


def _level(model: Model, key: str):
    levels = model.thinking_level_map
    assert levels is not None
    return levels.get(key)


class TestMoonshotaiProvider:
    def test_provider_id_and_name(self):
        provider = moonshotai_provider()
        assert provider.id == "moonshotai"
        assert provider.name == "Moonshot AI"

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
        model = MOONSHOTAI_MODELS["kimi-k3"]
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.MOONSHOTAI
        assert model.base_url == "https://api.moonshot.ai/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_kimi_k3_thinking_level_map(self):
        model = MOONSHOTAI_MODELS["kimi-k3"]
        # k3 当前支持 low/high/max
        assert _level(model, "max") == "max"
        assert _level(model, "low") == "low"
        assert _level(model, "high") == "high"
        assert _level(model, "minimal") is None
        assert _level(model, "medium") is None
        assert _level(model, "off") is None

    def test_compat_detected_as_openai_format(self):
        model = MOONSHOTAI_MODELS["kimi-k3"]
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI


class TestMoonshotaiCnModels:
    def test_kimi_k3_model_attributes(self):
        model = MOONSHOTAI_CN_MODELS["kimi-k3"]
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.MOONSHOTAI_CN
        assert model.base_url == "https://api.moonshot.cn/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_kimi_k3_thinking_level_map(self):
        model = MOONSHOTAI_CN_MODELS["kimi-k3"]
        # k3 当前支持 low/high/max
        assert _level(model, "max") == "max"
        assert _level(model, "low") == "low"
        assert _level(model, "high") == "high"
        assert _level(model, "minimal") is None
        assert _level(model, "off") is None

    def test_compat_is_openai_format(self):
        model = MOONSHOTAI_CN_MODELS["kimi-k3"]
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI


class TestKimiCodingModels:
    def test_k3_model_attributes(self):
        model = KIMI_CODING_MODELS["k3"]
        assert model.api == KnownApi.OPENAI_COMPLETIONS
        assert model.provider == KnownProvider.KIMI_CODING
        assert model.base_url == "https://api.kimi.com/coding/v1"
        assert model.context_window == 1_048_576
        assert model.reasoning is True

    def test_k3_thinking_level_map(self):
        model = KIMI_CODING_MODELS["k3"]
        assert _level(model, "max") == "max"
        assert _level(model, "high") == "high"
        assert _level(model, "low") == "low"
        assert _level(model, "off") is None

    def test_kimi_for_coding_supports_thinking_toggle(self):
        model = KIMI_CODING_MODELS["kimi-for-coding"]
        assert _level(model, "off") is None
        assert _level(model, "low") == "low"
        assert _level(model, "max") == "max"


class TestKimiBuildParams:
    def test_moonshotai_kimi_k3_reasoning_uses_openai_format(self):
        # pi.dev 实测：moonshot 目录的 k3 走 openai 格式 + effort 映射
        # （thinking_level_map 把 max 映射为 max 直发——k2.x 才是 deepseek）
        model = MOONSHOTAI_CN_MODELS["kimi-k3"]
        ctx = Context(messages=[UserMessage(content="hello")])
        options = OpenAICompletionsOptions(reasoning_effort="max")
        params = build_params(model, ctx, options)

        assert params["reasoning_effort"] == "max"
        assert "extra_body" not in params or "thinking" not in params.get(
            "extra_body", {}
        )


class _EnvContext:
    def __init__(self, values):
        self.values = values

    async def env(self, name):
        return self.values.get(name)

    async def file_exists(self, path):
        return False


@pytest.mark.asyncio
async def test_anthropic_auth_token_is_bearer_and_precedes_api_key():
    provider_auth = anthropic_provider().auth
    assert provider_auth is not None
    auth = provider_auth.api_key
    assert auth is not None
    resolve_input: ApiKeyAuthInput = {
        "ctx": _EnvContext(
            {
                "ANTHROPIC_AUTH_TOKEN": "bearer-token",
                "ANTHROPIC_API_KEY": "api-key",
            }
        ),
        "credential": None,
    }
    result = await auth.resolve(resolve_input)
    assert result is not None
    assert result.auth == {"headers": {"Authorization": "Bearer bearer-token"}}
    assert result.source == "ANTHROPIC_AUTH_TOKEN"


@pytest.mark.asyncio
async def test_anthropic_federation_is_returned_as_provider_env():
    provider_auth = anthropic_provider().auth
    assert provider_auth is not None
    auth = provider_auth.api_key
    assert auth is not None
    values = {
        "ANTHROPIC_FEDERATION_RULE_ID": "rule",
        "ANTHROPIC_ORGANIZATION_ID": "org",
        "ANTHROPIC_IDENTITY_TOKEN_FILE": "/identity-token",
        "ANTHROPIC_WORKSPACE_ID": "workspace",
    }
    resolve_input: ApiKeyAuthInput = {"ctx": _EnvContext(values), "credential": None}
    result = await auth.resolve(resolve_input)
    assert result is not None
    assert result.auth == {}
    assert result.env == values
    assert result.source == "workload identity federation"
