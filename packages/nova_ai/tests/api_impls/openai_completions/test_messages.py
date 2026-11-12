"""OpenAI Completions 的消息转换契约测试。"""

from nova_protocol import (
    AssistantMessage,
    Context,
    ImageContent,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
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
        content = messages[-1]["content"]
        assert content[0] == {
            "type": "text",
            "text": "Attached image(s) from tool result:",
        }
        images = content[1:]
        assert [item["type"] for item in images] == ["image_url", "image_url"]
        assert [item["image_url"]["url"] for item in images] == [
            "data:image/png;base64,first",
            "data:image/jpeg;base64,second",
        ]
