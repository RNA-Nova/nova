"""NovaBaseModel 出厂配置（base_model.py）测试。"""

from nova_protocol import KnownApi, KnownProvider, Model, ModelCost, TextContent


class TestEnumSerialization:
    def test_enum_serialized_as_value(self):
        """Enum 序列化用 .value（字符串）。"""
        m = Model(
            id="test",
            name="Test",
            api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI,
            base_url="https://api.openai.com",
            reasoning=False,
            input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000,
            max_tokens=4096,
        )
        data = m.model_dump()
        assert data["api"] == "openai-completions"
        assert data["provider"] == "openai"


class TestValidateAssignment:
    def test_assignment_by_name(self):
        """validate_assignment：允许属性名赋值并校验。"""
        t = TextContent(text="hello")
        t.text = "world"
        assert t.text == "world"
