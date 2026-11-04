"""openai_completions/moonshot_schema.py 测试——moonshot-flavored 子集规范化。

钉生产 400 的精确形状（``type`` 与 ``anyOf`` 同级共存），以及
detect_compat 的厂商判定与 convert_tools 的单点接线。
"""

from nova_protocol import (
    Context,
    KnownApi,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    Tool,
)

from nova_ai.api_impls.openai_completions import convert_tools
from nova_ai.api_impls.openai_completions.compat import detect_compat, get_compat
from nova_ai.api_impls.openai_completions.moonshot_schema import (
    normalize_moonshot_flavored_schema,
)


def _model(provider: str, base_url: str) -> Model:
    return Model(
        id="m",
        name="m",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=provider,
        base_url=base_url,
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
        context_window=128000,
        max_tokens=8192,
    )


def _tool(parameters) -> Tool:
    return Tool(name="t", description="d", parameters=parameters)


class TestNormalize:
    def test_root_type_anyof_supplemented(self):
        """生产 400 的精确形状：根部 type+anyOf → 缺型子项补型，父层型保留
        （探针实锤：根部 ``type: "object"`` 必选，共存合法）。"""
        schema = {
            "type": "object",
            "anyOf": [
                {"properties": {"a": {"type": "string"}}, "required": ["a"]},
                {"properties": {"b": {"type": "string"}}, "required": ["b"]},
            ],
        }
        result = normalize_moonshot_flavored_schema(schema)
        assert result["type"] == "object"
        assert all(item["type"] == "object" for item in result["anyOf"])
        # 子项原有内容不丢
        assert result["anyOf"][0]["required"] == ["a"]

    def test_nested_node_supplemented(self):
        schema = {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "anyOf": [{"enum": ["x"]}, {"pattern": "^y"}],
                }
            },
        }
        result = normalize_moonshot_flavored_schema(schema)
        node = result["properties"]["q"]
        assert node["type"] == "string"
        assert node["anyOf"][0]["type"] == "string"
        assert node["anyOf"][1]["type"] == "string"

    def test_item_with_own_type_untouched(self):
        """已声明 type 的子项不改判（恒真/矛盾是作者原义）。"""
        schema = {
            "type": "string",
            "anyOf": [{"type": "integer"}, {"enum": ["a"]}],
        }
        result = normalize_moonshot_flavored_schema(schema)
        assert result["anyOf"][0] == {"type": "integer"}
        assert result["anyOf"][1] == {"enum": ["a"], "type": "string"}

    def test_clean_schema_unchanged(self):
        schema = {
            "type": "object",
            "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
        assert normalize_moonshot_flavored_schema(schema) == schema

    def test_input_not_mutated(self):
        schema = {"type": "object", "anyOf": [{"properties": {}}]}
        normalize_moonshot_flavored_schema(schema)
        assert schema == {"type": "object", "anyOf": [{"properties": {}}]}


class TestDetect:
    def test_kimi_coding_provider_flagged(self):
        compat = detect_compat(_model("kimi-coding", "https://api.kimi.com/coding/v1"))
        assert compat.requires_moonshot_flavored_schema is True

    def test_kimi_base_url_flagged(self):
        compat = detect_compat(_model("custom", "https://api.kimi.com/v1"))
        assert compat.requires_moonshot_flavored_schema is True

    def test_moonshot_flagged(self):
        compat = detect_compat(_model("moonshotai", "https://api.moonshot.ai/v1"))
        assert compat.requires_moonshot_flavored_schema is True

    def test_openai_not_flagged(self):
        compat = detect_compat(_model("openai", "https://api.openai.com/v1"))
        assert compat.requires_moonshot_flavored_schema is False

    def test_explicit_override_wins(self):
        """显式 compat 覆盖检测值（通用 _pick 纪律）。"""
        model = _model("openai", "https://api.openai.com/v1").model_copy(
            update={
                "compat": OpenAICompletionsCompat(
                    requires_moonshot_flavored_schema=True
                )
            }
        )
        assert get_compat(model).requires_moonshot_flavored_schema is True


class TestConvertToolsWiring:
    _BAD_SCHEMA = {"type": "object", "anyOf": [{"properties": {}}]}

    def test_kimi_compat_normalizes_parameters(self):
        tools = convert_tools(
            [_tool(self._BAD_SCHEMA)],
            OpenAICompletionsCompat(requires_moonshot_flavored_schema=True),
        )
        params = tools[0]["function"]["parameters"]
        assert params["type"] == "object"
        assert params["anyOf"][0]["type"] == "object"

    def test_default_compat_passes_verbatim(self):
        tools = convert_tools([_tool(self._BAD_SCHEMA)], OpenAICompletionsCompat())
        assert tools[0]["function"]["parameters"] == self._BAD_SCHEMA
