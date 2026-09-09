"""GitHub Copilot 请求头构造测试（对齐 TS github-copilot-headers.ts）。"""

from nova_ai.api_impls._shared import (
    build_copilot_dynamic_headers,
    build_copilot_headers_from_messages,
    has_copilot_vision_input,
    infer_copilot_initiator,
)
from nova_ai.types import (
    AssistantMessage,
    ImageContent,
    TextContent,
    ToolResultMessage,
    UserMessage,
)


class TestInferCopilotInitiator:
    def test_empty_messages_is_user(self):
        assert infer_copilot_initiator([]) == "user"

    def test_last_user_message_is_user(self):
        messages = [UserMessage(content="hi")]
        assert infer_copilot_initiator(messages) == "user"

    def test_last_assistant_message_is_agent(self):
        messages = [
            UserMessage(content="hi"),
            AssistantMessage(content=[TextContent(text="ok")]),
        ]
        assert infer_copilot_initiator(messages) == "agent"

    def test_last_tool_result_is_agent(self):
        messages = [
            ToolResultMessage(tool_call_id="1", content=[TextContent(text="out")])
        ]
        assert infer_copilot_initiator(messages) == "agent"


class TestHasCopilotVisionInput:
    def test_user_image(self):
        messages = [
            UserMessage(content=[ImageContent(mime_type="image/png", data="aGVsbG8=")])
        ]
        assert has_copilot_vision_input(messages) is True

    def test_tool_result_image(self):
        messages = [
            ToolResultMessage(
                tool_call_id="1",
                content=[ImageContent(mime_type="image/png", data="aGVsbG8=")],
            )
        ]
        assert has_copilot_vision_input(messages) is True

    def test_text_only(self):
        messages = [UserMessage(content="hi")]
        assert has_copilot_vision_input(messages) is False

    def test_string_content_user(self):
        messages = [UserMessage(content="hi")]
        assert has_copilot_vision_input(messages) is False


class TestBuildCopilotDynamicHeaders:
    def test_base_headers(self):
        headers = build_copilot_dynamic_headers([], has_images=False)
        assert headers == {
            "X-Initiator": "user",
            "Openai-Intent": "conversation-edits",
        }

    def test_vision_header_added(self):
        headers = build_copilot_dynamic_headers([], has_images=True)
        assert headers["Copilot-Vision-Request"] == "true"

    def test_agent_initiator(self):
        messages = [AssistantMessage(content=[TextContent(text="ok")])]
        headers = build_copilot_dynamic_headers(messages, has_images=False)
        assert headers["X-Initiator"] == "agent"

    def test_from_messages(self):
        messages = [
            UserMessage(content=[ImageContent(mime_type="image/png", data="aGVsbG8=")])
        ]
        headers = build_copilot_headers_from_messages(messages)
        assert headers["X-Initiator"] == "user"
        assert headers["Copilot-Vision-Request"] == "true"
