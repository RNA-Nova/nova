"""anthropic_messages/compat.py 测试（默认值合并 + 覆盖 + tool_reference 代际门）。"""

from nova_protocol import AnthropicMessagesCompat, KnownApi, Model, ModelCost

from nova_ai.api_impls.anthropic_messages.compat import get_compat


def _model(
    model_id: str = "claude-sonnet-4-5",
    provider: str = "anthropic",
    compat=None,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider=provider,
        base_url="https://api.anthropic.com",
        reasoning=True,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
        compat=compat,
    )


class TestDefaults:
    def test_defaults_for_plain_anthropic_model(self):
        compat = get_compat(_model())
        assert compat.supports_eager_tool_input_streaming is True
        assert compat.supports_long_cache_retention is True
        assert compat.send_session_affinity_headers is False
        assert compat.supports_cache_control_on_tools is True
        assert compat.supports_temperature is True
        assert compat.supports_strict_tools is False
        assert compat.allow_empty_signature is False
        assert compat.force_adaptive_thinking is False


class TestOverrides:
    def test_explicit_fields_override_defaults(self):
        compat = get_compat(
            _model(
                compat=AnthropicMessagesCompat(
                    supports_temperature=False,
                    allow_empty_signature=True,
                    force_adaptive_thinking=True,
                )
            )
        )
        assert compat.supports_temperature is False
        assert compat.allow_empty_signature is True
        assert compat.force_adaptive_thinking is True
        # 未覆盖字段保持默认
        assert compat.supports_strict_tools is False

    def test_foreign_api_compat_ignored(self):
        """别的协议的 compat 对象不构成覆盖（判别归 Model.api）。"""
        from nova_protocol import OpenAICompletionsCompat

        compat = get_compat(
            _model(compat=OpenAICompletionsCompat(supports_store=False))
        )
        assert compat.supports_temperature is True
