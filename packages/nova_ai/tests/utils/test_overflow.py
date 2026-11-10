"""is_context_overflow 测试（对齐 TS isContextOverflow）。"""

from nova_protocol import AssistantMessage, KnownApi, NovaErrorKind, StopReason, Usage

from nova_ai.utils.overflow import is_context_overflow, is_recoverable_length


def _message(
    stop_reason: StopReason = StopReason.STOP,
    error_message: str = None,
    input: int = 0,
    output: int = 0,
    cache_read: int = 0,
) -> AssistantMessage:
    return AssistantMessage(
        role="assistant",
        content=[],
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="test",
        model="test",
        usage=Usage(
            input=input,
            output=output,
            cache_read=cache_read,
            cache_write=0,
            total_tokens=input + output + cache_read,
        ),
        stop_reason=stop_reason,
        error_message=error_message,
    )


class TestErrorPatterns:
    def test_anthropic_prompt_too_long(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="prompt is too long: 213462 tokens > 200000 maximum",
        )
        assert is_context_overflow(msg) is True

    def test_openai_exceeds_context_window(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Your input exceeds the context window of this model",
        )
        assert is_context_overflow(msg) is True

    def test_google_input_token_count(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="The input token count (1196265) exceeds the maximum number of tokens allowed (1048575)",
        )
        assert is_context_overflow(msg) is True

    def test_xai_maximum_prompt_length(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="This model's maximum prompt length is 131072 but the request contains 537812 tokens",
        )
        assert is_context_overflow(msg) is True

    def test_groq_reduce_length(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Please reduce the length of the messages or completion",
        )
        assert is_context_overflow(msg) is True

    def test_openrouter_maximum_context_length(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="This endpoint's maximum context length is 128000 tokens. However, you requested about 200000 tokens",
        )
        assert is_context_overflow(msg) is True

    def test_kimi_exceeded_model_token_limit(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Your request exceeded model token limit: 262144 (requested: 300000)",
        )
        assert is_context_overflow(msg) is True

    def test_mistral_too_large(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Prompt contains 200000 tokens ... too large for model with 128000 maximum context length",
        )
        assert is_context_overflow(msg) is True

    def test_ds4_configured_context_size(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Prompt has 200000 tokens, but the configured context size is 128000 tokens",
        )
        assert is_context_overflow(msg) is True

    def test_zai_model_context_window_exceeded(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="model_context_window_exceeded",
        )
        assert is_context_overflow(msg) is True

    def test_ollama_prompt_too_long(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="prompt too long; exceeded max context length by 1000 tokens",
        )
        assert is_context_overflow(msg) is True

    def test_cerebras_400_no_body(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="400 status code (no body)",
        )
        msg.provider = "cerebras"
        assert is_context_overflow(msg) is True

    def test_cerebras_413_no_body(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="413 status code (no body)",
        )
        msg.provider = "cerebras"
        assert is_context_overflow(msg) is True

    def test_bodyless_400_is_not_globally_overflow(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="400 status code (no body)",
        )
        assert is_context_overflow(msg) is False

    def test_generic_too_many_tokens(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="too many tokens in request",
        )
        assert is_context_overflow(msg) is True

    def test_generic_token_limit_exceeded(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="token limit exceeded",
        )
        assert is_context_overflow(msg) is True

    def test_litellm_context_window_exceeded(self):
        """LiteLLM pre-call 校验的溢出报文（pi #11494 同款回归）——
        通用模式 context[_ ]?window[_ ]?exceeded 覆盖，防词表回退。"""
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message=(
                "litellm.ContextWindowExceededError: litellm.BadRequestError: "
                "litellm._pre_call_checks: Context Window exceeded for given call. "
                "Max Input Tokens=262144, Got=265262"
            ),
        )
        assert is_context_overflow(msg) is True


class TestNonOverflowPatterns:
    def test_rate_limit_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="rate limit exceeded, please try again later",
        )
        assert is_context_overflow(msg) is False

    def test_too_many_requests_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="too many requests, please slow down",
        )
        assert is_context_overflow(msg) is False

    def test_throttling_error_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Throttling error: Too many tokens, please wait before trying again.",
        )
        assert is_context_overflow(msg) is False

    def test_service_unavailable_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="Service unavailable: Too many tokens, please wait",
        )
        assert is_context_overflow(msg) is False


class TestSilentOverflow:
    def test_silent_overflow_zai(self):
        msg = _message(
            stop_reason=StopReason.STOP,
            input=150000,
            cache_read=10000,
        )
        assert is_context_overflow(msg, context_window=128000) is True

    def test_not_silent_overflow(self):
        msg = _message(
            stop_reason=StopReason.STOP,
            input=100000,
            cache_read=10000,
        )
        assert is_context_overflow(msg, context_window=128000) is False

    def test_silent_overflow_requires_context_window(self):
        msg = _message(
            stop_reason=StopReason.STOP,
            input=150000,
        )
        assert is_context_overflow(msg) is False


class TestLengthStopOverflow:
    def test_xiaomi_mimo_truncation(self):
        """stop_reason=length + output=0 + input 满 context_window → 截断。"""
        msg = _message(
            stop_reason=StopReason.LENGTH,
            input=127000,
            cache_read=1000,
            output=0,
        )
        assert is_context_overflow(msg, context_window=128000) is True

    def test_length_with_output_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.LENGTH,
            input=127000,
            cache_read=1000,
            output=100,
        )
        assert is_context_overflow(msg, context_window=128000) is False

    def test_length_not_full_context_not_overflow(self):
        msg = _message(
            stop_reason=StopReason.LENGTH,
            input=100000,
            cache_read=1000,
            output=0,
        )
        assert is_context_overflow(msg, context_window=128000) is False


class TestErrorKindPriority:
    """kind 优先（铁律二）：``error_kind`` 非 None 时信任归一，不再做文本判定。"""

    def test_kind_overflow_wins_over_non_overflow_text(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="rate limit exceeded, please try again later",
        )
        msg.error_kind = NovaErrorKind.CONTEXT_OVERFLOW
        assert is_context_overflow(msg) is True

    def test_kind_rate_limited_skips_overflow_text(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="prompt is too long: 213462 tokens > 200000 maximum",
        )
        msg.error_kind = NovaErrorKind.RATE_LIMITED
        assert is_context_overflow(msg) is False

    def test_kind_none_falls_back_to_text(self):
        msg = _message(
            stop_reason=StopReason.ERROR,
            error_message="prompt is too long: 213462 tokens > 200000 maximum",
        )
        assert msg.error_kind is None
        assert is_context_overflow(msg) is True


def test_recoverable_length_requires_output_below_original_limit():
    message = _message(stop_reason=StopReason.LENGTH, output=64)
    assert is_recoverable_length(message, 128000) is True
    assert is_recoverable_length(message, 64) is False
    assert is_recoverable_length(message, 0) is False
    assert (
        is_recoverable_length(_message(stop_reason=StopReason.STOP, output=64), 128000)
        is False
    )
