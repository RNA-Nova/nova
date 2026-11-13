"""OpenAI Completions 的消息转换契约测试。"""

from nova_protocol import (
    AssistantMessage,
    Context,
    ImageContent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)

from nova_ai.api_impls.openai_completions import convert_messages, get_compat


class TestConvertMessages:
    """消息转换测试"""

    def test_user_message_string(self):
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
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content="hello")])
        messages = convert_messages(model, ctx, compat)

        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hello"

    def test_user_message_list(self):
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
        compat = get_compat(model)
        ctx = Context(messages=[UserMessage(content=[TextContent(text="hello")])])
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "user"
        content = messages[0]["content"]
        assert not isinstance(content, str)
        assert list(content)[0]["type"] == "text"

    def test_system_prompt(self):
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
        compat = get_compat(model)
        ctx = Context(
            system_prompt="You are helpful",
            messages=[UserMessage(content="hello")],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful"

    def test_assistant_message(self):
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
        compat = get_compat(model)
        ctx = Context(
            messages=[
                UserMessage(content="hello"),
                AssistantMessage(content=[TextContent(text="hi")]),
            ],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[1]["role"] == "assistant"
        assert messages[1].get("content") == "hi"

    def test_tool_result_message(self):
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
        compat = get_compat(model)
        ctx = Context(
            messages=[
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="search",
                    content=[TextContent(text="result")],
                ),
            ],
        )
        messages = convert_messages(model, ctx, compat)

        assert messages[0]["role"] == "tool"
        assert messages[0]["tool_call_id"] == "tc1"

    def test_consecutive_tool_results_move_images_to_one_user_in_order(self):
        model = Model(
            id="gpt-4",
            name="GPT-4",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text", "image"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        context = Context(
            messages=[
                AssistantMessage(
                    content=[
                        ToolCall(id="tc1", name="read", arguments={}),
                        ToolCall(id="tc2", name="read", arguments={}),
                    ]
                ),
                ToolResultMessage(
                    tool_call_id="tc1",
                    tool_name="read",
                    content=[
                        TextContent(text=""),
                        ImageContent(mime_type="image/png", data="first"),
                    ],
                ),
                ToolResultMessage(
                    tool_call_id="tc2",
                    tool_name="read",
                    content=[
                        TextContent(text="second text"),
                        ImageContent(mime_type="image/jpeg", data="second"),
                    ],
                ),
            ]
        )
        messages = convert_messages(model, context, get_compat(model))
        assert [message["role"] for message in messages] == [
            "assistant",
            "tool",
            "tool",
            "user",
        ]
        raw_content = messages[-1].get("content")
        assert raw_content is not None
        content = list(raw_content)
        assert content[0] == {
            "type": "text",
            "text": "Attached image(s) from tool result:",
        }
        images = content[1:]
        types = []
        urls = []
        for item in images:
            assert isinstance(item, dict)
            types.append(item.get("type"))
            image_url = item.get("image_url")
            assert isinstance(image_url, dict)
            urls.append(image_url.get("url"))
        assert types == ["image_url", "image_url"]
        assert urls == [
            "data:image/png;base64,first",
            "data:image/jpeg;base64,second",
        ]


# ---------------------------------------------------------------------------
# mid-conversation system / tool_call_id / cache_control
# ---------------------------------------------------------------------------


def test_mid_convo_system_passthrough_when_supported():
    """旗标为真：中途 system 消息逐条呈现为 system 条目。"""
    from nova_protocol import OpenAICompletionsCompat, SystemMessage

    from .conftest import _ctx, _model

    ctx = _ctx(
        UserMessage(content="q1"),
        SystemMessage(sections={"mode": "只读"}, timestamp=2),
        UserMessage(content="q2"),
    )
    ctx.system_prompt = "base prompt"
    params = convert_messages(
        _model(), ctx, OpenAICompletionsCompat(supports_mid_convo_system_messages=True)
    )
    roles = [m.get("role") for m in params]
    assert roles[0] == "system"
    assert roles.count("system") == 2
    update_entry = params[2]
    assert update_entry["role"] == "system"
    update_text = update_entry.get("content")
    assert isinstance(update_text, str)
    assert 'Updated system prompt section "mode"' in update_text


def test_mid_convo_system_collapses_when_unsupported():
    """旗标为假：中途变更折叠进头部，无多余 system 条目。"""
    from nova_protocol import OpenAICompletionsCompat, SystemMessage

    from .conftest import _ctx, _model

    ctx = _ctx(
        UserMessage(content="q1"),
        SystemMessage(sections={"mode": "只读"}, timestamp=2),
        UserMessage(content="q2"),
    )
    ctx.system_prompt = "base prompt"
    params = convert_messages(
        _model(), ctx, OpenAICompletionsCompat(supports_mid_convo_system_messages=False)
    )
    system_entries = [m for m in params if m.get("role") == "system"]
    assert len(system_entries) == 1
    system_text = system_entries[0].get("content")
    assert isinstance(system_text, str)
    assert "base prompt" in system_text
    assert "只读" in system_text


def test_normalize_tool_call_id_preserves_item_uniqueness():
    """同回合多个 Responses API 调用共享 call_id——item 段必须保留。"""
    from .conftest import _ctx, _model

    model = _model(provider="github-copilot")
    assistant = AssistantMessage(
        role="assistant",
        content=[
            ToolCall(type="toolCall", id="call_1|itemA", name="f", arguments={}),
            ToolCall(type="toolCall", id="call_1|itemB", name="f", arguments={}),
        ],
    )
    ctx = Context(messages=[UserMessage(content="hi"), assistant])
    params = convert_messages(model, ctx, OpenAICompletionsCompat())
    tool_messages = [m for m in params if m.get("role") == "assistant"]
    ids = [tc["id"] for m in tool_messages for tc in m.get("tool_calls", [])]
    assert len(ids) == 2
    assert len(set(ids)) == 2


def test_normalize_tool_call_id_truncates_over_40_with_hash():
    """超长管道 id（>40 字符）走 short_hash 截断分支。"""
    from .conftest import _ctx, _model

    model = _model(provider="github-copilot")
    long_call = "call_" + "x" * 40
    assistant = AssistantMessage(
        role="assistant",
        content=[
            ToolCall(type="toolCall", id=f"{long_call}|itemA", name="f", arguments={}),
            ToolCall(type="toolCall", id=f"{long_call}|itemB", name="f", arguments={}),
        ],
    )
    ctx = Context(messages=[UserMessage(content="hi"), assistant])
    params = convert_messages(model, ctx, OpenAICompletionsCompat())
    tool_messages = [m for m in params if m.get("role") == "assistant"]
    ids = [tc["id"] for m in tool_messages for tc in m.get("tool_calls", [])]
    assert all(len(i) <= 40 for i in ids)
    assert len(set(ids)) == 2


def test_non_null_assistant_content_flag_rewrites_null():
    """requiresNonNullAssistantContent：assistant 的 null content 改写空串。"""
    from nova_protocol import OpenAICompletionsCompat

    from .conftest import _model

    assistant = AssistantMessage(
        role="assistant",
        content=[ToolCall(type="toolCall", id="c1", name="f", arguments={})],
    )
    ctx = Context(messages=[UserMessage(content="hi"), assistant])

    model = _model()
    params = convert_messages(model, ctx, OpenAICompletionsCompat())
    assistant_param = next(m for m in params if m.get("role") == "assistant")
    assert assistant_param.get("content") is None

    params = convert_messages(
        model,
        ctx,
        OpenAICompletionsCompat(requires_non_null_assistant_content=True),
    )
    assistant_param = next(m for m in params if m.get("role") == "assistant")
    assert assistant_param.get("content") == ""


def test_cache_control_covers_tool_result_message():
    """openrouter+anthropic 模型：最后一条消息是 tool 结果时 marker 落在它上面。"""
    from nova_protocol import CacheRetention, ToolResultMessage

    from nova_ai.api_impls.openai_completions import (
        OpenAICompletionsOptions,
        build_params,
    )

    from .conftest import _model

    model = _model(
        provider="openrouter",
        id="anthropic/claude",
        base_url="https://openrouter.ai/api/v1",
        compat=OpenAICompletionsCompat(cache_control_format="anthropic"),
    )
    ctx = Context(
        messages=[
            UserMessage(content="run it"),
            AssistantMessage(
                role="assistant",
                content=[
                    ToolCall(type="toolCall", id="tc1", name="bash", arguments={})
                ],
            ),
            ToolResultMessage(tool_call_id="tc1", content=[TextContent(text="output")]),
        ]
    )
    params = build_params(
        model,
        ctx,
        OpenAICompletionsOptions(cache_retention=CacheRetention.SHORT),
        cache_retention=CacheRetention.SHORT,
    )
    last_tool = params["messages"][-1]
    assert last_tool["role"] == "tool"
    content = last_tool["content"]
    assert any(part.get("cache_control") for part in content)
