"""OpenAI Completions 的 compat 自动检测与显式覆盖测试。"""

import pytest
from nova_protocol import (
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    ThinkingFormat,
)

from nova_ai.api_impls.openai_completions import detect_compat, get_compat


class TestDetectCompat:
    """兼容性自动检测测试"""

    def test_openai_default(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI
        assert compat.supports_store is True
        assert compat.supports_usage_in_streaming is True

    def test_deepseek(self):
        model = Model(
            id="deepseek-v3",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="deepseek",
            base_url="https://api.deepseek.com",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_volcengine_deepseek(self):
        model = Model(
            id="deepseek-v3-2",
            name="DeepSeek",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.VOLCENGINE,
            base_url="https://ark.cn-beijing.volces.com/api/v3/",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.DEEPSEEK
        assert compat.requires_reasoning_content_on_assistant_messages is True

    def test_openrouter(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENROUTER,
            base_url="https://openrouter.ai/api/v1",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENROUTER

    def test_zai(self):
        model = Model(
            id="zai-model",
            name="ZAI",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.ZAI,
            base_url="https://api.z.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.ZAI

    def test_nvidia_detected(self):
        """NVIDIA：按非标准处理，不支持 reasoning_effort（对齐 TS）"""
        model = Model(
            id="nvidia-model",
            name="NVIDIA",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="nvidia",
            base_url="https://integrate.api.nvidia.com/v1",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.thinking_format == ThinkingFormat.OPENAI
        assert compat.supports_reasoning_effort is False
        assert compat.supports_store is False
        assert compat.max_tokens_field == "max_tokens"
        assert compat.supports_strict_mode is False
        assert compat.supports_long_cache_retention is False

    def test_openrouter_developer_role_only_for_prefixed_models(self):
        """OpenRouter：仅 anthropic//openai 前缀模型使用 developer role（对齐 TS）"""

        def _compat(model_id: str):
            return detect_compat(
                Model(
                    id=model_id,
                    name=model_id,
                    api=KnownApi.OPENAI_COMPLETIONS,
                    provider=KnownProvider.OPENROUTER,
                    base_url="https://openrouter.ai/api/v1",
                    reasoning=True,
                    input_types=["text"],
                    cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                    context_window=128000,
                    max_tokens=4096,
                )
            )

        assert _compat("anthropic/claude-opus").supports_developer_role is True
        assert _compat("openai/gpt-5").supports_developer_role is True
        assert _compat("meta-llama/llama-4").supports_developer_role is False

    def test_mistral_uses_standard_defaults(self):
        """Mistral：不再做 provider 特判，按标准 OpenAI 检测（对齐 TS）"""
        model = Model(
            id="mistral-model",
            name="Mistral",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.MISTRAL,
            base_url="https://api.mistral.ai",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.requires_thinking_as_text is False
        assert compat.requires_tool_result_name is False
        assert compat.max_tokens_field == "max_completion_tokens"
        assert compat.supports_store is True
        assert compat.supports_developer_role is True

    def test_grok_no_reasoning_effort(self):
        model = Model(
            id="grok-2",
            name="Grok",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.XAI,
            base_url="https://api.x.ai",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)
        assert compat.supports_reasoning_effort is False

    def test_ant_ling_detected_from_url(self):
        """ant-ling 经 URL 检测，且不支持 reasoning_effort"""
        model = Model(
            id="ant-model",
            name="AntLing",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.ant-ling.com/v1",
            reasoning=True,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        compat = detect_compat(model)

        assert compat.thinking_format == "ant-ling"
        assert compat.supports_reasoning_effort is False


class TestGetCompat:
    """get_compat 合并逻辑测试"""

    def test_model_compat_overrides(self):
        model = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
            compat=OpenAICompletionsCompat(supports_store=False),
        )
        compat = get_compat(model)
        assert compat.supports_store is False
        assert compat.supports_usage_in_streaming is True  # 自动检测的值

    def test_openai_responses_compat_rejected(self):
        """跨协议 compat 实例在模型边界响亮失败，不隐式忽略。"""
        from nova_protocol import OpenAIResponsesCompat

        with pytest.raises(ValueError, match="compat"):
            Model(
                id="test",
                name="Test",
                api=KnownApi.OPENAI_COMPLETIONS,
                provider=KnownProvider.OPENAI,
                base_url="https://api.openai.com",
                reasoning=False,
                input_types=["text"],
                cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
                context_window=128000,
                max_tokens=4096,
                compat=OpenAIResponsesCompat(),
            )
