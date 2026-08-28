"""流式 JSON 片段解析测试。"""

from nova_ai.utils import parse_streaming_json


class TestParseStreamingJson:
    def test_complete_object(self):
        assert parse_streaming_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_empty_and_none(self):
        assert parse_streaming_json(None) == {}
        assert parse_streaming_json("") == {}
        assert parse_streaming_json("   ") == {}

    def test_truncated_mid_string(self):
        # 工具参数流式到达时的典型截断：json_repair 补全字符串与括号
        assert parse_streaming_json('{"query": "hello wor') == {"query": "hello wor"}

    def test_truncated_mid_structure(self):
        assert parse_streaming_json('{"a": {"b": [1, 2') == {"a": {"b": [1, 2]}}

    def test_invalid_returns_empty_object(self):
        assert parse_streaming_json("not json at all {") == {}

    def test_array_top_level(self):
        result = parse_streaming_json("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_progressive_parsing(self):
        """模拟流式累积：同一前缀的多个截断点都应可解析"""
        full = '{"name": "read", "arguments": {"path": "/tmp/a.py", "offset": 10}}'
        for i in range(1, len(full) + 1):
            result = parse_streaming_json(full[:i])
            assert isinstance(result, (dict, list))
