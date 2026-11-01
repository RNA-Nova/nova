"""模型与用量词汇（model.py）测试——Model/Cost/Usage/ModelCost。"""

from nova_protocol import (
    Cost,
    KnownApi,
    KnownProvider,
    Model,
    ModelCost,
    OpenAICompletionsCompat,
    Usage,
)


class TestUsageAndCost:
    """用量和成本。"""

    def test_cost_default(self):
        c = Cost()
        assert c.input == 0.0
        assert c.output == 0.0
        assert c.cache_read == 0.0
        assert c.cache_write == 0.0
        assert c.total == 0.0

    def test_usage_default(self):
        u = Usage()
        assert u.input == 0
        assert u.output == 0
        assert u.cache_read == 0
        assert u.cache_write == 0
        assert u.total_tokens == 0
        assert isinstance(u.cost, Cost)

    def test_usage_with_cost(self):
        u = Usage(input=10, output=20, cost=Cost(input=0.01, output=0.02, total=0.03))
        assert u.input == 10
        assert u.cost.total == 0.03


def _model_dict(**overrides):
    base = {
        "id": "test-model",
        "name": "Test Model",
        "api": KnownApi.OPENAI_COMPLETIONS,
        "provider": KnownProvider.OPENAI,
        "base_url": "https://api.openai.com",
        "reasoning": False,
        "input_types": ["text"],
        "cost": ModelCost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
        "context_window": 128000,
        "max_tokens": 4096,
    }
    return base | overrides


class TestModel:
    def test_model_creation(self):
        m = Model(**_model_dict())
        assert m.id == "test-model"
        assert m.reasoning is False
        assert m.thinking_level_map is None

    def test_model_with_thinking_level_map(self):
        m = Model(
            **_model_dict(
                reasoning=True, thinking_level_map={"high": "high", "xhigh": "max"}
            )
        )
        assert m.thinking_level_map == {"high": "high", "xhigh": "max"}

    def test_model_auto_compat(self):
        """未显式给 compat 时按 api 自动补默认实例。"""
        m = Model(**_model_dict())
        assert m.compat is not None
        assert isinstance(m.compat, OpenAICompletionsCompat)
