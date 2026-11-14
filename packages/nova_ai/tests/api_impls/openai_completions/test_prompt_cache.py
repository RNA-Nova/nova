"""openai_completions 缓存与兼容行为单元测试。"""

from typing import Any, Dict, List, Optional

import pytest
from nova_ai.api_impls import openai_completions
from nova_ai.api_impls._shared.prompt_cache import CacheControl
from nova_ai.api_impls.openai_completions import (
    OpenAICompletionsOptions,
    _apply_anthropic_cache_control,
    _get_compat_cache_control,
    build_params,
    clamp_openai_prompt_cache_key,
    create_client,
    parse_chunk_usage,
)
from nova_ai.stream_options import StreamOptions
from nova_protocol import (
    AssistantMessage,
    CacheRetention,
    Context,
    Cost,
    KnownApi,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    StopReason,
    Tool,
    Usage,
    UserMessage,
)
from openai.types.chat import ChatCompletionToolParam


def _make_model(
    model_id: str = "test",
    provider: str = "test",
    base_url: str = "https://example.com",
    compat: Optional[OpenAICompletionsCompat] = None,
    reasoning: bool = False,
    input_types: Optional[list] = None,
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


def _openai_compat(model: Model) -> OpenAICompletionsCompat:
    compat = model.compat
    assert isinstance(compat, OpenAICompletionsCompat)
    return compat


def _function_name(tool: ChatCompletionToolParam) -> str:
    function = tool.get("function")
    assert function is not None
    name = function.get("name")
    assert name is not None
    return name


class TestClampOpenAIPromptCacheKey:
    def test_short_key_unchanged(self):
        assert clamp_openai_prompt_cache_key("abc") == "abc"

    def test_long_key_truncated(self):
        key = "x" * 100
        result = clamp_openai_prompt_cache_key(key)
        assert result is not None
        assert len(result) == 64
        assert result == "x" * 64

    def test_none_returns_none(self):
        assert clamp_openai_prompt_cache_key(None) is None


class TestGetCompatCacheControl:
    def test_no_cache_control_for_non_anthropic(self):
        compat = OpenAICompletionsCompat(cache_control_format=None)
        assert _get_compat_cache_control(compat, CacheRetention.LONG) is None

    def test_no_cache_control_when_retention_none(self):
        compat = OpenAICompletionsCompat(cache_control_format="anthropic")
        assert _get_compat_cache_control(compat, CacheRetention.NONE) is None

    def test_short_retention_no_ttl(self):
        compat = OpenAICompletionsCompat(cache_control_format="anthropic")
        control = _get_compat_cache_control(compat, CacheRetention.SHORT)
        assert control == {"type": "ephemeral"}

    def test_long_retention_with_ttl(self):
        compat = OpenAICompletionsCompat(
            cache_control_format="anthropic",
            supports_long_cache_retention=True,
        )
        control = _get_compat_cache_control(compat, CacheRetention.LONG)
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
        cache_control: CacheControl = {"type": "ephemeral"}
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

    def test_prompt_cache_key_mode_enabled_sends_for_proxy(self):
        """enabled：兼容代理即使非 openai 官方端点、short 保留也发缓存键
        （对齐 pi 0f4d5e960——compatible proxies 场景）。"""
        model = _make_model(
            base_url="https://proxy.example.com/v1",
            compat=OpenAICompletionsCompat(
                supports_long_cache_retention=False,
                prompt_cache_key_mode="enabled",
            ),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.SHORT,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body", {})
        assert extra.get("prompt_cache_key") == "session-123"

    def test_prompt_cache_key_mode_disabled_never_sends(self):
        """disabled：即使 openai 官方端点也永不发（对齐 pi 0f4d5e960）。"""
        model = _make_model(
            base_url="https://api.openai.com/v1",
            compat=OpenAICompletionsCompat(prompt_cache_key_mode="disabled"),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.SHORT,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body") or {}
        assert "prompt_cache_key" not in extra

    def test_prompt_cache_key_mode_default_auto_unchanged(self):
        """缺省 auto：非官方端点 + short 保留不发（旧启发式语义不回归）。"""
        model = _make_model(
            base_url="https://proxy.example.com/v1",
            compat=OpenAICompletionsCompat(supports_long_cache_retention=False),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        options = OpenAICompletionsOptions(
            session_id="session-123",
            cache_retention=CacheRetention.SHORT,
        )
        params = build_params(model, ctx, options)

        extra = params.get("extra_body") or {}
        assert "prompt_cache_key" not in extra

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

    def test_cloudflare_no_api_key_preserves_caller_auth_headers(self):
        """api_key 为空时不得产出 "Bearer None"，也不得覆盖调用方自带的 cf-aig-authorization。"""
        model = _make_model(
            provider="cloudflare-ai-gateway",
            base_url="https://gateway.ai.cloudflare.com",
            compat=OpenAICompletionsCompat(),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        client = create_client(
            model,
            ctx,
            api_key=None,
            options_headers={"cf-aig-authorization": "Bearer caller-key"},
        )

        headers = client.default_headers
        assert headers.get("cf-aig-authorization") == "Bearer caller-key"

    def test_cloudflare_authorization_lookup_is_case_insensitive(self):
        """调用方传小写 authorization 时同样复用，不回填空串。"""
        model = _make_model(
            provider="cloudflare-ai-gateway",
            base_url="https://gateway.ai.cloudflare.com",
            compat=OpenAICompletionsCompat(),
        )
        ctx = Context(messages=[UserMessage(content="hi")])
        client = create_client(
            model,
            ctx,
            api_key="cf-key",
            options_headers={"authorization": "Bearer caller-auth"},
        )

        headers = client.default_headers
        assert headers.get("Authorization") == "Bearer caller-auth"
        assert headers.get("cf-aig-authorization") == "Bearer cf-key"

    def test_header_overrides_are_case_insensitive(self):
        model = _make_model().model_copy(
            update={"headers": {"X-Trace": "model", "X-Model": "yes"}}
        )
        client = create_client(
            model,
            Context(messages=[UserMessage(content="hi")]),
            api_key="sk",
            options_headers={"x-trace": "options"},
        )
        trace_keys = [key for key in client.default_headers if key.lower() == "x-trace"]
        assert trace_keys == ["x-trace"]
        assert client.default_headers["x-trace"] == "options"
        assert client.default_headers["X-Model"] == "yes"

    def test_none_header_uses_sdk_omit_sentinel(self):
        from openai import Omit

        client = create_client(
            _make_model(),
            Context(messages=[UserMessage(content="hi")]),
            api_key="sk",
            options_headers={"User-Agent": None},
        )
        assert isinstance(client.default_headers["User-Agent"], Omit)


class TestMidConvoToolAdditions:
    """additions 模式（pi resolveTranscriptTools 对位）：SystemMessage 驱动。"""

    def _model_kimi(self):
        return _make_model(
            compat=OpenAICompletionsCompat(
                supports_mid_convo_system_messages=True,
                supports_mid_convo_tool_additions=True,
            )
        )

    @staticmethod
    def _tool(name):
        return Tool(
            name=name,
            description=f"{name} desc",
            parameters={"type": "object", "properties": {}},
        )

    def test_system_message_tools_added_emits_kimi_message(self):
        """中途 SystemMessage 的 toolsAdded → kimi 工具型 system 消息。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import SystemMessage

        model = self._model_kimi()
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                SystemMessage(tools_added=[self._tool("dynamic_tool")], timestamp=1),
            ],
            tools=[self._tool("static_tool")],
        )
        messages = convert_messages(model, ctx, _openai_compat(model))

        kimi_msgs = [
            m for m in messages if m.get("role") == "system" and m.get("tools")
        ]
        assert len(kimi_msgs) == 1
        tools = kimi_msgs[0].get("tools")
        assert tools is not None
        assert _function_name(tools[0]) == "dynamic_tool"

    def test_additions_not_duplicated_in_top_level(self):
        """就地新增不进顶层列表（顶层 = 折叠段状态——仅初始工具）。"""
        from nova_protocol import SystemMessage

        model = self._model_kimi()
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                SystemMessage(tools_added=[self._tool("dynamic_tool")], timestamp=1),
            ],
            tools=[self._tool("static_tool")],
        )
        params = build_params(model, ctx)

        top_level_names = {t["function"]["name"] for t in params.get("tools") or []}
        assert top_level_names == {"static_tool"}

    def test_removal_folds_state_into_top_level(self):
        """移除是非增量变更：顶层取移除后状态，其后新增就地锚定。"""
        from nova_protocol import SystemMessage, ToolReference

        model = self._model_kimi()
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                SystemMessage(
                    tools_removed=[ToolReference(name="static_tool")], timestamp=1
                ),
                SystemMessage(tools_added=[self._tool("dynamic_tool")], timestamp=2),
            ],
            tools=[self._tool("static_tool")],
        )
        params = build_params(model, ctx)

        # 折叠段（head + 移除）后工具为空 → 顶层无工具
        assert not params.get("tools")
        # 移除后的新增就地发 kimi 消息
        kimi_msgs = [
            m
            for m in params["messages"]
            if m.get("role") == "system" and m.get("tools")
        ]
        assert len(kimi_msgs) == 1
        tools = kimi_msgs[0].get("tools")
        assert tools is not None
        assert _function_name(tools[0]) == "dynamic_tool"

    def test_redefinition_is_non_additive(self):
        """同名重声明是非增量变更：顶层直接换血，不发 kimi 消息。"""
        from nova_protocol import SystemMessage

        model = self._model_kimi()
        redefined = Tool(
            name="static_tool",
            description="v2 desc",
            parameters={"type": "object", "properties": {}},
        )
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                SystemMessage(tools_added=[redefined], timestamp=1),
            ],
            tools=[self._tool("static_tool")],
        )
        params = build_params(model, ctx)

        top = {t["function"]["name"]: t for t in params.get("tools") or []}
        assert top["static_tool"]["function"]["description"] == "v2 desc"
        kimi_msgs = [
            m
            for m in params["messages"]
            if m.get("role") == "system" and m.get("tools")
        ]
        assert kimi_msgs == []

    def test_tool_result_empty_content_uses_no_output_placeholder(self):
        """无文本且无图片的 tool result 应使用 '(no tool output)' 占位。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import ToolResultMessage

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
        messages = convert_messages(model, ctx, _openai_compat(model))

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].get("content") == "(no tool output)"

    def test_tool_result_whitespace_only_uses_no_output_placeholder(self):
        """纯空白 tool result（如 bash 返回裸 \r\n）视同空输出占位——
        纯空白内容会被部分 provider 400 且会话内每轮复现（pi #8720 实证）。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import TextContent, ToolResultMessage

        model = _make_model()
        ctx = Context(
            messages=[
                UserMessage(content="run"),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="t1",
                    content=[TextContent(text="\r\n  ")],
                ),
            ],
        )
        messages = convert_messages(model, ctx, _openai_compat(model))

        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].get("content") == "(no tool output)"

    def test_kimi_tool_message_anchors_at_system_message_position(self):
        """kimi 工具消息锚在 SystemMessage 自己的转录位置（含图片工具结果
        的用户块之后不窜位）。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import (
            ImageContent,
            SystemMessage,
            TextContent,
            ToolResultMessage,
        )

        tool_def = Tool(
            name="dynamic_tool",
            description="dynamic",
            parameters={"type": "object", "properties": {}},
        )
        model = _make_model(
            compat=OpenAICompletionsCompat(
                supports_mid_convo_system_messages=True,
                supports_mid_convo_tool_additions=True,
            ),
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
                ),
                SystemMessage(tools_added=[tool_def], timestamp=1),
            ],
        )
        messages = convert_messages(model, ctx, _openai_compat(model))

        roles = [m.get("role") for m in messages]
        # user(run) -> tool -> user(image) -> system(kimi 工具消息)
        assert roles == ["user", "tool", "user", "system"]
        assert messages[-1].get("tools") is not None


class TestConvertMessagesUserContent:
    def test_empty_user_content_array_is_skipped(self):
        """空 content 数组的 user 消息应直接跳过，对齐 TS。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import TextContent

        model = _make_model()
        ctx = Context(
            messages=[
                UserMessage(content=[]),
                UserMessage(content=[TextContent(type="text", text="hello")]),
            ],
        )
        messages = convert_messages(model, ctx, _openai_compat(model))

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == [{"type": "text", "text": "hello"}]

    def test_unsupported_image_user_content_gets_placeholder(self):
        """只有图片但模型不支持时保留占位，避免消息完全丢失。"""
        from nova_ai.api_impls.openai_completions import convert_messages
        from nova_protocol import ImageContent

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
        messages = convert_messages(model, ctx, _openai_compat(model))

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        content = messages[0].get("content")
        assert isinstance(content, list)
        assert (
            content[0].get("text") == "(image omitted: model does not support images)"
        )


class TestProviderErrorFormatting:
    def test_normalize_openai_sdk_error_extracts_status_and_body(self):
        from nova_ai.utils.errors import normalize_provider_error

        class FakeError(Exception):
            status = 403
            body = '{"error": {"message": "rate limit"}}'

        norm = normalize_provider_error(FakeError("403 status code (no body)"))
        assert norm.status == 403
        assert norm.body == '{"error": {"message": "rate limit"}}'
        assert "rate limit" not in norm.message
        assert norm.message_carries_body is False

    def test_format_provider_error_includes_prefix_status_body(self):
        from nova_ai.utils.errors import NormalizedProviderError, format_provider_error

        norm = NormalizedProviderError(
            message="bad request",
            status=400,
            body='{"error": "invalid model"}',
            message_carries_body=False,
        )
        text = format_provider_error(norm, prefix="kimi")
        assert text == 'kimi (400): {"error": "invalid model"}'

    def test_format_skips_body_when_message_already_carries_it(self):
        from nova_ai.utils.errors import NormalizedProviderError, format_provider_error

        norm = NormalizedProviderError(
            message='400: {"error": "invalid model"}',
            status=400,
            body='{"error": "invalid model"}',
            message_carries_body=True,
        )
        text = format_provider_error(norm)
        assert text == '400: {"error": "invalid model"}'
