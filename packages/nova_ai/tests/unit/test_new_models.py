"""新 provider 模型详细字段测试（moonshotai / moonshotai-cn / kimi-coding）。"""

from nova_ai.providers.kimi_coding import KIMI_CODING_MODELS, get_kimi_coding_model
from nova_ai.providers.moonshotai import MOONSHOTAI_MODELS, get_moonshotai_model
from nova_ai.providers.moonshotai_cn import (
    MOONSHOTAI_CN_MODELS,
    get_moonshotai_cn_model,
)
from nova_ai.types import KnownApi, KnownProvider


class TestMoonshotaiModels:
    def test_models_count(self):
        assert len(MOONSHOTAI_MODELS) == 10

    def test_all_models_have_required_fields(self):
        for model_id, model in MOONSHOTAI_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.MOONSHOTAI
            assert model.base_url == "https://api.moonshot.ai/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0
            assert model.cost is not None

    def test_kimi_k3_fields(self):
        m = get_moonshotai_model("kimi-k3")
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131072
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["off"] is None

    def test_kimi_k2_5_fields(self):
        m = get_moonshotai_model("kimi-k2.5")
        assert m.id == "kimi-k2.5"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 262144
        assert m.max_tokens == 262144

    def test_kimi_k2_thinking_fields(self):
        m = get_moonshotai_model("kimi-k2-thinking")
        assert m.id == "kimi-k2-thinking"
        assert m.reasoning is True
        assert m.input_types == ["text"]

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_moonshotai_model("nonexistent")


class TestMoonshotaiCnModels:
    def test_models_count(self):
        assert len(MOONSHOTAI_CN_MODELS) == 10

    def test_all_models_have_required_fields(self):
        for model_id, model in MOONSHOTAI_CN_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.MOONSHOTAI_CN
            assert model.base_url == "https://api.moonshot.cn/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0

    def test_kimi_k3_fields(self):
        m = get_moonshotai_cn_model("kimi-k3")
        assert m.id == "kimi-k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131_072
        # k3 当前支持 low/high/max
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["low"] == "low"
        assert m.thinking_level_map["high"] == "high"
        assert m.thinking_level_map["minimal"] is None
        assert m.thinking_level_map["medium"] is None
        assert m.thinking_level_map["off"] is None

    def test_kimi_k2_6_fields(self):
        m = get_moonshotai_cn_model("kimi-k2.6")
        assert m.id == "kimi-k2.6"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144

    def test_kimi_k2_7_code_fields(self):
        m = get_moonshotai_cn_model("kimi-k2.7-code")
        assert m.id == "kimi-k2.7-code"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 262_144

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_moonshotai_cn_model("nonexistent")


class TestKimiCodingModels:
    def test_models_count(self):
        assert len(KIMI_CODING_MODELS) == 5

    def test_all_models_have_required_fields(self):
        for model_id, model in KIMI_CODING_MODELS.items():
            assert model.id == model_id
            assert model.name
            assert model.api == KnownApi.OPENAI_COMPLETIONS
            assert model.provider == KnownProvider.KIMI_CODING
            assert model.base_url == "https://api.kimi.com/coding/v1"
            assert isinstance(model.reasoning, bool)
            assert model.input_types
            assert model.context_window > 0
            assert model.max_tokens > 0

    def test_k3_fields(self):
        m = get_kimi_coding_model("k3")
        assert m.id == "k3"
        assert m.name == "Kimi K3"
        assert m.reasoning is True
        assert m.input_types == ["text", "image"]
        assert m.context_window == 1_048_576
        assert m.max_tokens == 131_072
        # k3 当前支持 low/high/max
        assert m.thinking_level_map["max"] == "max"
        assert m.thinking_level_map["low"] == "low"
        assert m.thinking_level_map["high"] == "high"
        assert m.thinking_level_map["minimal"] is None
        assert m.thinking_level_map["medium"] is None
        assert m.thinking_level_map["off"] is None

    def test_kimi_for_coding_fields(self):
        m = get_kimi_coding_model("kimi-for-coding")
        assert m.id == "kimi-for-coding"
        assert m.name == "Kimi For Coding"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 32768

    def test_kimi_for_coding_highspeed_fields(self):
        m = get_kimi_coding_model("kimi-for-coding-highspeed")
        assert m.id == "kimi-for-coding-highspeed"
        assert m.name == "Kimi For Coding HighSpeed"
        assert m.reasoning is True
        assert m.context_window == 262_144
        assert m.max_tokens == 32768

    def test_get_model_not_found(self):
        import pytest

        with pytest.raises(KeyError):
            get_kimi_coding_model("nonexistent")
