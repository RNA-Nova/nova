"""
注册表测试
"""

from nova_ai.registry import (
    ApiRegistry,
    ModelRegistry,
    has_api_adapter,
    get_models_by_provider,
    reset_registry,
)
from nova_ai.types.enums import KnownApi, KnownProvider
from nova_ai.types import Model, ModelCost


class TestApiRegistry:
    """API 提供者注册表测试"""

    def test_register_and_get(self):
        registry = ApiRegistry()

        class FakeAdapter:
            api = "test-api"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        registry.register(FakeAdapter())
        record = registry.get("test-api")
        assert record is not None
        assert record.api == "test-api"

    def test_register_api_adapter_record(self):
        registry = ApiRegistry()

        class FakeAdapter:
            api = "test-api"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        record = FakeAdapter()
        registry.register(record)
        assert registry.get("test-api") is not None

    def test_get_not_found(self):
        registry = ApiRegistry()
        assert registry.get("nonexistent") is None

    def test_list(self):
        registry = ApiRegistry()

        class FakeAdapter1:
            api = "api1"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        class FakeAdapter2:
            api = "api2"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        registry.register(FakeAdapter1())
        registry.register(FakeAdapter2())
        assert set(registry.list()) == {"api1", "api2"}

    def test_unregister(self):
        registry = ApiRegistry()

        class FakeAdapter:
            api = "test"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        registry.register(FakeAdapter())
        removed = registry.unregister("test")
        assert removed is not None
        assert registry.get("test") is None

    def test_has_adapter(self):
        registry = ApiRegistry()

        class FakeAdapter:
            api = "test"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        registry.register(FakeAdapter())
        assert registry.has_adapter("test") is True
        assert registry.has_adapter("none") is False

    def test_clear(self):
        registry = ApiRegistry()

        class FakeAdapter:
            api = "test"
            stream = lambda self, *args, **kwargs: None
            stream_simple = lambda self, *args, **kwargs: None

        registry.register(FakeAdapter())
        registry.clear()
        assert registry.list() == []


class TestModelRegistry:
    """模型注册表测试"""

    def test_register_and_get(self):
        registry = ModelRegistry()
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        registry.register_model("openai", model)
        assert registry.get_model("openai", "test") == model

    def test_get_not_found(self):
        registry = ModelRegistry()
        assert registry.get_model("openai", "none") is None

    def test_list_providers(self):
        registry = ModelRegistry()
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        registry.register_model("openai", model)
        assert registry.list_providers() == ["openai"]

    def test_find_model_by_id(self):
        registry = ModelRegistry()
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        registry.register_model("openai", model)
        assert registry.get_model_by_id("test") == model

    def test_remove_model(self):
        registry = ModelRegistry()
        model = Model(
            id="test", name="Test", api=KnownApi.OPENAI_COMPLETIONS,
            provider=KnownProvider.OPENAI, base_url="https://api.openai.com",
            reasoning=False, input_types=["text"],
            cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
            context_window=128000, max_tokens=4096,
        )
        registry.register_model("openai", model)
        assert registry.remove_model("openai", "test") is True
        assert registry.get_model("openai", "test") is None


class TestBuiltinRegistration:
    """内置注册测试"""

    def test_register_all_builtins(self):
        reset_registry()
        # 内置 OpenAI Completions provider 应该已注册
        assert has_api_adapter(KnownApi.OPENAI_COMPLETIONS)
        # Volcengine 模型应该已注册
        assert get_models_by_provider(KnownProvider.VOLCENGINE.value)

    def test_reset_registry(self):
        reset_registry()
        assert has_api_adapter(KnownApi.OPENAI_COMPLETIONS)
