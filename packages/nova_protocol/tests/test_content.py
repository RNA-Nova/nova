"""内容类型（content.py）测试——默认值、序列化形状与判别字段。"""

from nova_protocol import ImageContent, TextContent, ThinkingContent, ToolCall


class TestTextContent:
    def test_default(self):
        t = TextContent()
        assert t.type == "text"
        assert t.text == ""
        assert t.text_signature is None

    def test_serialization(self):
        t = TextContent(text="hello", text_signature="sig123")
        data = t.model_dump()
        assert data["text"] == "hello"
        assert data["text_signature"] == "sig123"


class TestThinkingContent:
    def test_fields(self):
        t = ThinkingContent(thinking="let me think", redacted=True)
        assert t.type == "thinking"
        assert t.thinking == "let me think"
        assert t.redacted is True


class TestImageContent:
    def test_fields(self):
        img = ImageContent(mime_type="image/png", data="base64data")
        assert img.type == "image"
        assert img.mime_type == "image/png"


class TestToolCall:
    def test_fields_and_dump_exclusions(self):
        tc = ToolCall(id="tc1", name="get_weather", arguments={"city": "北京"})
        assert tc.type == "toolCall"
        assert tc.arguments == {"city": "北京"}
        # partial_args 和 stream_index 不参与序列化
        data = tc.model_dump()
        assert "partial_args" not in data
        assert "stream_index" not in data
