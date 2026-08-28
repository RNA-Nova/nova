"""openai_completions 缓存与兼容行为单元测试。"""

from typing import Any, Dict, List

import pytest

from nova_ai.api_impls import openai_completions
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    _apply_anthropic_cache_control,
    _get_compat_cache_control,
    build_params,
    clamp_openai_prompt_cache_key,
    create_client,
    parse_chunk_usage,
)
from nova_ai.types import (
    AssistantMessage,
    Context,
    Cost,
    Model,
    ModelCost,
    StopReason,
    Usage,
    UserMessage,
)
from nova_ai.types.compat import OpenAICompletionsCompat
from nova_ai.types.enums import CacheRetention, KnownApi
from nova_ai.types.messages import Tool
from nova_ai.types.stream_options import StreamOptions


def _make_model(
    model_id: str = "test",
    provider: str = "test",
    base_url: str = "https://example.com",
    compat: OpenAICompletionsCompat = None,
    reasoning: bool = False,
    input_types: list = None,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url=base_url,
        reasoning=reasoning,
        input_types=input_types if input_types is not None else ["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=4096,
        compat=compat,
    )


class TestClampOpenAIPromptCacheKey:
    def test_short_key_unchanged(self):
        assert clamp_openai_prompt_cache_key("abc") == "abc"

    def test_long_key_truncated(self):
        key = "x" * 100
        result = clamp_openai_prompt_cache_key(key)
        assert len(result) == 64
        assert result == "x" * 64

    def test_none_returns_none(self):
        assert clamp_openai_prompt_cache_key(None) is None


class TestGetCompatCacheControl:
    def test_no_cache_control_for_non_anthropic(self):
        compat = OpenAICompletionsCompat(cache_control_format=None)
        assert _get_compat_cache_control(compat, "long") is None

    def test_no_cache_control_when_retention_none(self):
        compat = OpenAICompletionsCompat(cache_control_format="anthropic")
        assert _get_compat_cache_control(compat, "none") is None

    def test_short_retention_no_ttl(self):
        compat = OpenAICompletionsCompat(cache_control_format="anthropic")
        control = _get_compat_cache_control(compat, "short")
        assert control == {"type": "ephemeral"}

    def test_long_retention_with_ttl(self):
        compat = OpenAICompletionsCompat(
            cache_control_format="anthropic",
            supports_long_cache_retention=True,
        )
        control = _get_compat_cache_control(compat, "long")
        assert control == {"type": "ephemeral", "ttl": "1h"}


class TestApplyAnthropicCacheControl:
    def test_applies_to_system_tool_and_last_message(self):
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        tools = [
            {"type": "function", "function": {"name": "t1"}},
            {"type": "function", "function": {"name": "t2"}},
        ]
        cache_control = {"type": "ephemeral"}
        _apply_anthropic_cache_control(messages, tools, cache_control)

        # system prompt 被标记
        assert messages[0]["content"][0]["cache_control"] == cache_control
        # 最后一条 tool 被标记
        assert tools[-1]["cache_control"] == cache_control
        # 最后一条对话消息被标记
        assert messages[-1]["content"][0]["cache_control"] == cache_control
        # 中间消息未被标记
        assert "cache_control" not in messages[1]["content"][0]


class TestBuildParamsCache:
    def test_prompt_cache_key_in_extra_body_with_long_retention(self):
        model = _make_model(
            base_url="https://example.com",
            compat=OpenAICompletionsCompat(supports_long_cache_retention=True),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.LONG,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body", {})
        assert extra.get("prompt_cache_key") == "session-123"
        assert extra.get("prompt_cache_retention") == "24h"
        # 非标准字段不应出现在顶层（OpenAI Python SDK 会拒绝）
        assert "prompt_cache_key" not in params
        assert "prompt_cache_retention" not in params

    def test_prompt_cache_key_clamped(self):
        model = _make_model(
            compat=OpenAICompletionsCompat(supports_long_cache_retention=True),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="s" * 100,
            cache_retention=CacheRetention.LONG,
        )
        params = build_params(model, ctx, options)

        key = params.get("extra_body", {}).get("prompt_cache_key")
        assert len(key) == 64

    def test_no_prompt_cache_key_for_short_retention(self):
        model = _make_model(
            compat=OpenAICompletionsCompat(supports_long_cache_retention=True),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.SHORT,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body") or {}
        assert "prompt_cache_key" not in extra
        assert "prompt_cache_retention" not in extra

    def test_openai_com_domain_uses_cache_key_even_short(self):
        model = _make_model(
            base_url="https://api.openai.com/v1",
            compat=OpenAICompletionsCompat(supports_long_cache_retention=False),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.SHORT,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body", {})
        assert extra.get("prompt_cache_key") == "session-123"
        assert "prompt_cache_retention" not in extra

    def test_cache_retention_from_env(self):
        model = _make_model(
            compat=OpenAICompletionsCompat(supports_long_cache_retention=True),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            env={"NOVA_CACHE_RETENTION": "long"},
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body", {})
        assert extra.get("prompt_cache_key") == "session-123"
        assert extra.get("prompt_cache_retention") == "24h"


class TestApplyChunkUsage:
    def test_does_not_subtract_cache_write_from_cached(self):
        """对齐 TS：cached_tokens 与 cache_write_tokens 是独立字段，不互相减去。"""
        output = AssistantMessage(
            role="assistant",
            content=[],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="test",
            model="test",
            usage=Usage(
                input=0,
                output=0,
                cache_read=0,
                cache_write=0,
                total_tokens=0,
                cost=Cost(),
            ),
            stop_reason=StopReason.STOP,
            timestamp=0,
        )

        class FakeUsage:
            prompt_tokens = 1000
            completion_tokens = 100
            prompt_tokens_details = type(
                "Details",
                (),
                {"cached_tokens": 800, "cache_write_tokens": 200},
            )()

        model = _make_model()
        usage = parse_chunk_usage(FakeUsage(), model)

        assert usage.cache_read == 800
        assert usage.cache_write == 200
        assert usage.input == 0  # 1000 - 800 - 200
        assert usage.total_tokens == 1100


class TestCreateClientSessionAffinity:
    def test_openai_format_headers(self):
        model = _make_model(
            base_url="https://api.openai.com/v1",
            compat=OpenAICompletionsCompat(
                send_session_affinity_headers=True,
                session_affinity_format="openai",
            ),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        client = create_client(model, ctx, api_key="sk", session_id="sid")

        headers = client.default_headers
        assert headers.get("session_id") == "sid"
        assert headers.get("x-client-request-id") == "sid"
        assert headers.get("x-session-affinity") == "sid"

    def test_openrouter_format_header(self):
        model = _make_model(
            base_url="https://openrouter.ai/api/v1",
            compat=OpenAICompletionsCompat(
                send_session_affinity_headers=True,
                session_affinity_format="openrouter",
            ),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        client = create_client(model, ctx, api_key="sk", session_id="sid")

        headers = client.default_headers
        assert headers.get("x-session-id") == "sid"
        assert "session_id" not in headers

    def test_no_api_key_when_authorization_header_present(self):
        model = _make_model(
            base_url="https://gateway.ai.cloudflare.com",
            compat=OpenAICompletionsCompat(),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        client = create_client(
            model,
            ctx,
            api_key=None,
            options_headers={"Authorization": "Bearer token"},
        )

        assert client.api_key == "unused"


class TestDeferredToolsMode:
    def test_kimi_deferred_tools_adds_system_message(self):
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_ai.types.content import TextContent
        from nova_ai.types.messages import ToolResultMessage

        tool_def = Tool(
            name="dynamic_tool",
            description="dynamic",
            parameters={"type": "object", "properties": {}},
        )
        model = _make_model(
            compat=OpenAICompletionsCompat(deferred_tools_mode="kimi"),
        )
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="t1",
                    content=[TextContent(type="text", text="ok")],
                    added_tool_names=["dynamic_tool"],
                ),
            ],
            tools=[tool_def],
        )
        messages = convert_messages(model, ctx, model.compat)

        # 找到 system 消息且包含 deferred tools
        system_msgs = [m for m in messages if m.get("role") == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].get("tools") is not None
        assert system_msgs[0]["tools"][0]["function"]["name"] == "dynamic_tool"

    def test_kimi_deferred_tools_not_duplicated_in_top_level_tools(self):
        """deferred tools 不应同时出现在顶层 tools 和 system 消息中。"""
        from nova_ai.types.content import TextContent
        from nova_ai.types.messages import ToolResultMessage

        tool_def = Tool(
            name="dynamic_tool",
            description="dynamic",
            parameters={"type": "object", "properties": {}},
        )
        model = _make_model(
            compat=OpenAICompletionsCompat(deferred_tools_mode="kimi"),
        )
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="t1",
                    content=[TextContent(type="text", text="ok")],
                    added_tool_names=["dynamic_tool"],
                ),
            ],
            tools=[tool_def],
        )
        params = build_params(model, ctx)

        top_level_tools = params.get("tools") or []
        top_level_names = {t["function"]["name"] for t in top_level_tools}
        assert "dynamic_tool" not in top_level_names

    def test_tool_result_empty_content_uses_no_output_placeholder(self):
        """无文本且无图片的 tool result 应使用 '(no tool output)' 占位。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_ai.types.messages import ToolResultMessage

        model = _make_model()
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="t1",
                    content=[],
                ),
            ],
        )
        messages = convert_messages(model, ctx, model.compat)

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "(no tool output)"

    def test_kimi_deferred_tools_inserted_after_image_blocks(self):
        """deferred tools system 消息应紧跟在 imageBlocks 之后，对齐 TS。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_ai.types.content import ImageContent, TextContent
        from nova_ai.types.messages import ToolResultMessage

        tool_def = Tool(
            name="dynamic_tool",
            description="dynamic",
            parameters={"type": "object", "properties": {}},
        )
        model = _make_model(
            compat=OpenAICompletionsCompat(deferred_tools_mode="kimi"),
            input_types=["text", "image"],
        )
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="t1",
                    content=[
                        TextContent(type="text", text="ok"),
                        ImageContent(
                            type="image",
                            mime_type="image/png",
                            data="aGVsbG8=",
                        ),
                    ],
                    added_tool_names=["dynamic_tool"],
                ),
            ],
            tools=[tool_def],
        )
        messages = convert_messages(model, ctx, model.compat)

        roles = [m.get("role") for m in messages]
        # 期望顺序: user(run) -> tool -> user(image) -> system(deferred tools)
        assert roles == ["user", "tool", "user", "system"]
        assert messages[-1].get("tools") is not None


class TestConvertMessagesUserContent:
    def test_empty_user_content_array_is_skipped(self):
        """空 content 数组的 user 消息应直接跳过，对齐 TS。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_ai.types.content import TextContent

        model = _make_model()
        ctx = Context(
            messages=[
                UserMessage(content=[]),
                UserMessage(content=[TextContent(type="text", text="hello")]),
            ],
        )
        messages = convert_messages(model, ctx, model.compat)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == [{"type": "text", "text": "hello"}]

    def test_unsupported_image_user_content_gets_placeholder(self):
        """只有图片但模型不支持时保留占位，避免消息完全丢失。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_ai.types.content import ImageContent

        model = _make_model(input_types=["text"])
        ctx = Context(
            messages=[
                UserMessage(
                    content=[
                        ImageContent(
                            type="image",
                            mime_type="image/png",
                            data="aGVsbG8=",
                        ),
                    ]
                ),
            ],
        )
        messages = convert_messages(model, ctx, model.compat)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert (
            messages[0]["content"][0]["text"]
            == "(image omitted: model does not support images)"
        )


class TestProviderErrorFormatting:
    def test_normalize_openai_sdk_error_extracts_status_and_body(self):
        from nova_ai.utils.error_body import normalize_provider_error

        class FakeError(Exception):
            status = 403
            body = '{"error": {"message": "rate limit"}}'

        norm = normalize_provider_error(FakeError("403 status code (no body)"))
        assert norm.status == 403
        assert norm.body == '{"error": {"message": "rate limit"}}'
        assert "rate limit" not in norm.message
        assert norm.message_carries_body is False

    def test_format_provider_error_includes_prefix_status_body(self):
        from nova_ai.utils.error_body import (
            NormalizedProviderError,
            format_provider_error,
        )

        norm = NormalizedProviderError(
            message="bad request",
            status=400,
            body='{"error": "invalid model"}',
            message_carries_body=False,
        )
        text = format_provider_error(norm, prefix="kimi")
        assert text == 'kimi (400): {"error": "invalid model"}'

    def test_format_skips_body_when_message_already_carries_it(self):
        from nova_ai.utils.error_body import (
            NormalizedProviderError,
            format_provider_error,
        )

        norm = NormalizedProviderError(
            message='400: {"error": "invalid model"}',
            status=400,
            body='{"error": "invalid model"}',
            message_carries_body=True,
        )
        text = format_provider_error(norm)
        assert text == '400: {"error": "invalid model"}'
