"""anthropic_messages/params.py 测试（build_params 穷举）。

锁定字段级契约：max_tokens 必填回落、system 顶层块与 OAuth 身份序、
temperature 与思考互斥、思考三形态、工具即刻/延迟组、tool_choice、
metadata、sampling_params 出货边界。
"""

from nova_protocol import (
    AnthropicMessagesCompat,
    AssistantMessage,
    CacheRetention,
    Context,
    KnownApi,
    Model,
    ModelCost,
    StopReason,
    SystemMessage,
    TextContent,
    Tool,
    ToolResultMessage,
    Usage,
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


class TestMidConvoSystemMessages:
    """中途 system 消息（pi resolveTranscript 对位）：穿过=held-back 注入；
    折叠=并入顶层 system 块。"""

    def _ctx(self):
        return Context(
            system_prompt="base prompt",
            messages=[
                UserMessage(content="q1"),
                SystemMessage(sections={"mode": "只读"}, timestamp=2),
                AssistantMessage(
                    role="assistant",
                    content=[TextContent(type="text", text="a1")],
                    stop_reason=StopReason.STOP,
                    usage=Usage(),
                ),
                UserMessage(content="q2"),
            ],
        )

    def test_passthrough_held_back_before_assistant(self):
        from nova_protocol import AnthropicMessagesCompat

        params = build_params(
            _model(
                compat=AnthropicMessagesCompat(supports_mid_convo_system_messages=True)
            ),
            self._ctx(),
        )
        roles = [m["role"] for m in params["messages"]]
        assert "system" in roles
        # system 条目在 assistant 之前（held-back，不插在 tool 对之间）
        sys_idx = roles.index("system")
        assert roles[sys_idx + 1] == "assistant"
        entry = params["messages"][sys_idx]
        assert 'Updated system prompt section "mode"' in entry["content"][0]["text"]
        # 头部仍是顶层块
        assert params["system"][0]["text"] == "base prompt"

    def test_collapse_folds_into_top_level_system(self):
        params = build_params(_model(), self._ctx())  # 旗标缺省 false
        assert params["system"][0]["text"] == "base prompt\n\n只读"
        assert all(m["role"] != "system" for m in params["messages"])

    def test_held_back_survives_dropped_assistant(self):
        """空 assistant（转换后无块被丢弃）不触发 flush——pending 延后到
        下一个非空 assistant（pi #11091 同款）。"""
        from nova_protocol import AnthropicMessagesCompat

        ctx = Context(
            system_prompt="base",
            messages=[
                UserMessage(content="q1"),
                SystemMessage(sections={"mode": "只读"}, timestamp=2),
                # 空文本 assistant——转换后无块，被丢弃
                AssistantMessage(
                    role="assistant",
                    content=[TextContent(type="text", text="   ")],
                    stop_reason=StopReason.STOP,
                    usage=Usage(),
                ),
                # 非空 assistant——pending 应在此前 flush
                AssistantMessage(
                    role="assistant",
                    content=[TextContent(type="text", text="real")],
                    stop_reason=StopReason.STOP,
                    usage=Usage(),
                ),
                UserMessage(content="q2"),
            ],
        )
        params = build_params(
            _model(
                compat=AnthropicMessagesCompat(supports_mid_convo_system_messages=True)
            ),
            ctx,
        )
        roles = [m["role"] for m in params["messages"]]
        sys_idx = roles.index("system")
        # system 条目直接在"real"assistant 之前（跳过了空 assistant）
        assert roles[sys_idx + 1] == "assistant"
        entry = params["messages"][sys_idx]
        assert 'Updated system prompt section "mode"' in entry["content"][0]["text"]

    """mid-convo effort（对齐 TS：旗标 + 标记注入 + 历史回放）。"""


class TestMidConvoEffort:
    """mid-convo effort（对齐 TS：旗标 + 标记注入 + 历史回放）。"""

    def _compat(self, **over):
        from nova_protocol import AnthropicMessagesCompat

        return AnthropicMessagesCompat(
            force_adaptive_thinking=True,
            supports_mid_convo_effort=True,
            **over,
        )

    def _assistant_with_effort(self, effort, stop_reason=StopReason.STOP):
        return AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="ok")],
            # api/provider 必须属主本端——pi 同款三重过滤（跨源转录不回放档位）
            api=KnownApi.ANTHROPIC_MESSAGES,
            provider="anthropic",
            stop_reason=stop_reason,
            provider_thinking_level=effort,
            usage=Usage(),
        )

    def test_markers_inserted_when_flag_on(self):
        """旗标为真：历史档（仅完整回复）前插标记 + 当前档收尾（对齐 TS
        insertThinkingLevelMessages）。"""
        from nova_protocol import Usage

        ctx = Context(
            messages=[
                UserMessage(content="q1"),
                self._assistant_with_effort("high"),
                UserMessage(content="q2"),
                self._assistant_with_effort("low", stop_reason=StopReason.ERROR),
                UserMessage(content="q3"),
                self._assistant_with_effort("medium"),
            ]
        )
        params = build_params(_model(compat=self._compat()), ctx)
        roles = [m["role"] for m in params["messages"]]
        # system 标记数 = 2 条完整历史 + 1 条当前收尾（ERROR 的不计）
        assert roles.count("system") == 3
        efforts = [
            m["output_config"]["effort"]
            for m in params["messages"]
            if m["role"] == "system"
        ]
        assert efforts == ["high", "medium", "high"]  # 当前缺省 high

    def test_no_markers_when_flag_off(self):
        from nova_protocol import AnthropicMessagesCompat

        ctx = Context(
            messages=[UserMessage(content="q"), self._assistant_with_effort("high")]
        )
        params = build_params(
            _model(compat=AnthropicMessagesCompat(force_adaptive_thinking=True)), ctx
        )
        assert all(m["role"] != "system" for m in params["messages"])

    def test_tool_use_reply_gets_marker(self):
        """TOOL_USE 收尾的回复同样记档（pi wire 索引语义——无 stop 过滤；
        且跨 provider 的档位不回放）。"""
        from nova_protocol import ToolCall, ToolResultMessage

        tool_use_reply = AssistantMessage(
            role="assistant",
            content=[ToolCall(id="t1", name="search", arguments={})],
            api=KnownApi.ANTHROPIC_MESSAGES,
            provider="anthropic",
            stop_reason=StopReason.TOOL_USE,
            provider_thinking_level="medium",
            usage=Usage(),
        )
        ctx = Context(
            messages=[
                UserMessage(content="q1"),
                tool_use_reply,
                ToolResultMessage(
                    tool_call_id="t1",
                    tool_name="search",
                    content=[TextContent(type="text", text="r")],
                ),
            ]
        )
        params = build_params(_model(compat=self._compat()), ctx)
        markers = [
            m
            for m in params["messages"]
            if m["role"] == "system" and "output_config" in m
        ]
        # tool_use 档（medium）+ 当前档收尾（缺省 high）
        assert [m["output_config"]["effort"] for m in markers] == ["medium", "high"]

    def test_foreign_provider_level_not_replayed(self):
        """跨 provider 的 provider_thinking_level 不回放（pi 三重过滤）。"""
        foreign = AssistantMessage(
            role="assistant",
            content=[TextContent(type="text", text="ok")],
            api=KnownApi.OPENAI_COMPLETIONS,
            provider="other",
            stop_reason=StopReason.STOP,
            provider_thinking_level="high",
            usage=Usage(),
        )
        ctx = Context(messages=[UserMessage(content="q"), foreign])
        params = build_params(_model(compat=self._compat()), ctx)
        markers = [
            m
            for m in params["messages"]
            if m["role"] == "system" and "output_config" in m
        ]
        assert [m["output_config"]["effort"] for m in markers] == ["high"]  # 仅当前档

    def test_explicit_effort_wins_and_disabled_suppresses(self):
        """options.effort 覆盖缺省 high；thinking_enabled=False 时标记全抑。"""
        from nova_protocol import Usage

        # 显式 effort
        ctx = Context(messages=[UserMessage(content="q")])
        params = build_params(
            _model(compat=self._compat()),
            ctx,
            AnthropicOptions(thinking_enabled=True, effort="low"),
        )
        system_efforts = [
            m["output_config"]["effort"]
            for m in params["messages"]
            if m["role"] == "system"
        ]
        assert system_efforts == ["low"]
        # 显式关闭 → 无标记
        params = build_params(
            _model(compat=self._compat()),
            ctx,
            AnthropicOptions(thinking_enabled=False),
        )
        assert all(m["role"] != "system" for m in params["messages"])

    def test_top_level_effort_fixed_low_with_block_binding(self):
        """effortMessages 分支：顶层 output_config 固定 low（组织 cap 账户
        拒绝高档——对齐 pi e3bc05e87），真档位由标记承载；thinking 为
        adaptive + block_binding（前缀失配可 drop）。"""
        ctx = Context(messages=[UserMessage(content="q")])
        params = build_params(
            _model(compat=self._compat()),
            ctx,
            AnthropicOptions(thinking_enabled=True, effort="max"),
        )
        assert params["thinking"] == {
            "type": "adaptive",
            "display": "summarized",
            "block_binding": {"prefix_mismatch_behavior": "drop_block"},
        }
        assert params["output_config"] == {"effort": "low"}
        # 真档位在收尾标记上
        markers = [
            m["output_config"]["effort"]
            for m in params["messages"]
            if m["role"] == "system"
        ]
        assert markers == ["max"]

    def test_temperature_suppressed_for_mid_convo_effort(self):
        """mid-convo effort 模型不发 temperature（对齐 TS 温度条件）。"""
        params = build_params(
            _model(compat=self._compat()),
            Context(messages=[UserMessage(content="q")]),
            AnthropicOptions(temperature=0.5),
        )
        assert "temperature" not in params

    def test_off_null_keeps_thinking_and_markers(self):
        """off 显式 null（端点不支持关）：thinking_enabled=False 也不发
        disabled、标记照走（对齐 TS thinkingDisabled 判定）。"""
        model = _model(
            compat=self._compat(),
            thinking_level_map={"off": None, "high": "high"},
        )
        params = build_params(
            model,
            Context(messages=[UserMessage(content="q")]),
            AnthropicOptions(thinking_enabled=False),
        )
        assert params.get("thinking", {}).get("type") == "adaptive"
        assert params["output_config"] == {"effort": "low"}
        assert any(m["role"] == "system" for m in params["messages"])

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

    def test_current_tools_sent_in_plain_form(self):
        """普通形态（无 INLINE 旗标）：当前工具集原样下发、无占位工具。"""
        ctx = Context(
            messages=[UserMessage(content="hi")],
            tools=[_tool("read"), _tool("search")],
        )
        params = build_params(_model(), ctx)
        names = [t["name"] for t in params["tools"]]
        assert names == ["read", "search"]
        assert all("defer_loading" not in t for t in params["tools"])


class TestInlineToolsParams:
    """INLINE_TOOLS 形态（对齐 pi buildParams 的 nativeToolChanges 分支）。"""

    def _compat(self, **over):
        return AnthropicMessagesCompat(
            supports_mid_convo_system_messages=True,
            supports_mid_convo_tool_changes=True,
            **over,
        )

    def test_fixed_tool_list_with_placeholder(self):
        """顶层工具 = 初始工具 + 常驻占位（defer_loading），此后不变。"""
        from nova_ai.api_impls.anthropic_messages.params import (
            DEFERRED_TOOL_PLACEHOLDER,
        )

        ctx = Context(messages=[UserMessage(content="hi")], tools=[_tool("read")])
        params = build_params(_model(compat=self._compat()), ctx)
        assert [t["name"] for t in params["tools"]] == [
            "read",
            DEFERRED_TOOL_PLACEHOLDER["name"],
        ]
        assert params["tools"][-1]["defer_loading"] is True

    def test_mid_convo_addition_blocks_in_messages(self):
        """中途 system 消息的工具变更 → tool_addition/removal 块按值下发。"""
        from nova_protocol import SystemMessage, ToolReference

        ctx = Context(
            messages=[
                UserMessage(content="q1"),
                SystemMessage(
                    tools_added=[_tool("search")],
                    tools_removed=[ToolReference(name="write")],
                    timestamp=2,
                ),
                UserMessage(content="q2"),
            ],
            tools=[_tool("read"), _tool("write")],
        )
        params = build_params(_model(compat=self._compat()), ctx)
        # 顶层固定：初始（read+write）+ 占位——中途增删不进顶层
        assert [t["name"] for t in params["tools"]][:2] == ["read", "write"]
        system_msgs = [m for m in params["messages"] if m["role"] == "system"]
        assert len(system_msgs) == 1
        types = [b["type"] for b in system_msgs[0]["content"]]
        assert types == ["tool_removal", "tool_addition"]
        assert system_msgs[0]["content"][1]["tool"]["definition"]["name"] == "search"

    def test_gate_requires_initial_tools(self):
        """无初始工具（无锚）→ 回退普通形态，无占位（Anthropic 拒绝全 defer）。"""
        ctx = Context(messages=[UserMessage(content="hi")])
        params = build_params(_model(compat=self._compat()), ctx)
        assert "tools" not in params

    def test_gate_requires_tool_changes_flag(self):
        """缺 supports_mid_convo_tool_changes → 普通形态。"""
        ctx = Context(messages=[UserMessage(content="hi")], tools=[_tool("read")])
        params = build_params(
            _model(
                compat=AnthropicMessagesCompat(
                    supports_mid_convo_system_messages=True,
                    supports_mid_convo_tool_changes=False,
                )
            ),
            ctx,
        )
        names = [t["name"] for t in params["tools"]]
        assert names == ["read"]


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
