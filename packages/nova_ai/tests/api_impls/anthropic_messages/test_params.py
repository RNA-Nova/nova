"""anthropic_messages/params.py 测试（build_params 穷举）。

锁定字段级契约：max_tokens 必填回落、system 顶层块与 OAuth 身份序、
temperature 与思考互斥、思考三形态、工具即刻/延迟组、tool_choice、
metadata、sampling_params 出货边界。
"""

from nova_protocol import (
    AnthropicMessagesCompat,
    CacheRetention,
    Context,
    KnownApi,
    Model,
    ModelCost,
    TextContent,
    Tool,
    ToolResultMessage,
    UserMessage,
)

from nova_ai.api_impls.anthropic_messages.compat import get_compat
from nova_ai.api_impls.anthropic_messages.options import AnthropicOptions
from nova_ai.api_impls.anthropic_messages.params import build_params


def _model(compat=None, reasoning=True, thinking_level_map=None) -> Model:
    return Model(
        id="claude-sonnet-4-5",
        name="m",
        api=KnownApi.ANTHROPIC_MESSAGES,
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=reasoning,
        input_types=["text"],
        cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
        context_window=200000,
        max_tokens=64000,
        compat=compat,
        thinking_level_map=thinking_level_map,
    )


def _tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"{name} desc",
        parameters={"type": "object", "properties": {}, "required": []},
    )


class TestRequiredFields:
    def test_max_tokens_falls_back_to_model_cap(self):
        params = build_params(_model(), Context(messages=[UserMessage(content="hi")]))
        assert params["max_tokens"] == 64000
        assert params["stream"] is True
        assert params["model"] == "claude-sonnet-4-5"

    def test_explicit_max_tokens_wins(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(max_tokens=1024),
        )
        assert params["max_tokens"] == 1024


class TestSystemBlocks:
    def test_system_prompt_top_level_block(self):
        # 默认 retention=short → 带 ephemeral cache_control（对齐 TS 默认路径；
        # 完整关闭见 test_cache_retention_none_disables）
        params = build_params(
            _model(),
            Context(system_prompt="你是助手", messages=[UserMessage(content="hi")]),
        )
        assert params["system"] == [
            {
                "type": "text",
                "text": "你是助手",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_oauth_identity_comes_first(self):
        """OAuth 令牌的强制身份块在首位（对齐 TS isOAuthToken 分支）。"""
        params = build_params(
            _model(),
            Context(system_prompt="你是助手", messages=[UserMessage(content="hi")]),
            is_oauth=True,
        )
        assert params["system"][0]["text"].startswith("You are Claude Code")
        assert params["system"][1]["text"] == "你是助手"

    def test_cache_control_on_system_blocks(self):
        params = build_params(
            _model(),
            Context(system_prompt="长提示", messages=[UserMessage(content="hi")]),
            cache_retention=CacheRetention.SHORT,
        )
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_retention_long_gets_ttl(self):
        params = build_params(
            _model(),
            Context(system_prompt="长提示", messages=[UserMessage(content="hi")]),
            cache_retention=CacheRetention.LONG,
        )
        assert params["system"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_cache_retention_none_disables(self):
        params = build_params(
            _model(),
            Context(system_prompt="长提示", messages=[UserMessage(content="hi")]),
            cache_retention=CacheRetention.NONE,
        )
        assert "cache_control" not in params["system"][0]

    def test_long_retention_unsupported_drops_ttl(self):
        params = build_params(
            _model(compat=AnthropicMessagesCompat(supports_long_cache_retention=False)),
            Context(system_prompt="长提示", messages=[UserMessage(content="hi")]),
            cache_retention=CacheRetention.LONG,
        )
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}


class TestTemperature:
    def test_temperature_present(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(temperature=0.5),
        )
        assert params["temperature"] == 0.5

    def test_temperature_omitted_when_thinking_enabled(self):
        """扩展思考与 temperature 不兼容（对齐 TS）。"""
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(temperature=0.5, thinking_enabled=True),
        )
        assert "temperature" not in params

    def test_temperature_omitted_when_unsupported(self):
        params = build_params(
            _model(compat=AnthropicMessagesCompat(supports_temperature=False)),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(temperature=0.5),
        )
        assert "temperature" not in params


class TestThinking:
    def test_budget_based_thinking(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=True, thinking_budget_tokens=4096),
        )
        assert params["thinking"] == {
            "type": "enabled",
            "budget_tokens": 4096,
            "display": "summarized",
        }

    def test_budget_default_1024(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=True),
        )
        assert params["thinking"]["budget_tokens"] == 1024

    def test_adaptive_thinking_with_effort(self):
        params = build_params(
            _model(compat=AnthropicMessagesCompat(force_adaptive_thinking=True)),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=True, effort="max"),
        )
        assert params["thinking"] == {"type": "adaptive", "display": "summarized"}
        assert params["output_config"] == {"effort": "max"}

    def test_thinking_disabled_explicit(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=False),
        )
        assert params["thinking"] == {"type": "disabled"}

    def test_thinking_disabled_suppressed_when_off_null(self):
        """level_map 显式 null 化 "off" = 端点不支持显式关闭——不发 disabled
        （对齐 TS ``thinkingLevelMap?.off !== null`` 判定）。"""
        params = build_params(
            _model(thinking_level_map={"off": None, "high": "high"}),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=False),
        )
        assert "thinking" not in params

    def test_no_thinking_field_for_non_reasoning_model(self):
        params = build_params(
            _model(reasoning=False),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(thinking_enabled=True),
        )
        assert "thinking" not in params


class TestTools:
    def test_immediate_tools(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")], tools=[_tool("read")]),
        )
        assert params["tools"][0]["name"] == "read"
        assert params["tools"][0]["eager_input_streaming"] is True

    def test_deferred_tools_defer_loading(self):
        """added_tool_names 引入的工具进延迟组，带 defer_loading。"""
        result = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="r")],
            added_tool_names=["search"],
        )
        ctx = Context(
            messages=[UserMessage(content="hi"), result],
            tools=[_tool("read"), _tool("search")],
        )
        params = build_params(_model(), ctx)
        by_name = {t["name"]: t for t in params["tools"]}
        assert "defer_loading" not in by_name["read"]
        assert by_name["search"]["defer_loading"] is True

    def test_all_deferred_falls_back_to_immediate(self):
        """全部延迟 = 端点拿不到工具定义——回退全即刻（对齐 TS）。"""
        result = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="r")],
            added_tool_names=["search"],
        )
        ctx = Context(
            messages=[UserMessage(content="hi"), result], tools=[_tool("search")]
        )
        params = build_params(_model(), ctx)
        assert params["tools"][0]["name"] == "search"
        assert "defer_loading" not in params["tools"][0]

    def test_no_defer_when_tool_references_unsupported(self):
        model = _model(compat=AnthropicMessagesCompat(supports_tool_references=False))
        result = ToolResultMessage(
            tool_call_id="t1",
            tool_name="tool_search",
            content=[TextContent(text="r")],
            added_tool_names=["search"],
        )
        ctx = Context(
            messages=[UserMessage(content="hi"), result], tools=[_tool("search")]
        )
        params = build_params(model, ctx)
        assert "defer_loading" not in params["tools"][0]


class TestMisc:
    def test_tool_choice_string_and_dict(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")], tools=[_tool("read")]),
            AnthropicOptions(tool_choice="any"),
        )
        assert params["tool_choice"] == {"type": "any"}

        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")], tools=[_tool("read")]),
            AnthropicOptions(tool_choice={"type": "tool", "name": "read"}),
        )
        assert params["tool_choice"] == {"type": "tool", "name": "read"}

    def test_metadata_user_id_only(self):
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(metadata={"user_id": "u1", "other": "ignored"}),
        )
        assert params["metadata"] == {"user_id": "u1"}

    def test_sampling_params_merge_at_boundary(self):
        """sampling_params 出货边界合入（自由负载可覆盖命名字段——对齐 TS）。"""
        params = build_params(
            _model(),
            Context(messages=[UserMessage(content="hi")]),
            AnthropicOptions(sampling_params={"top_p": 0.9}),
        )
        assert params["top_p"] == 0.9
