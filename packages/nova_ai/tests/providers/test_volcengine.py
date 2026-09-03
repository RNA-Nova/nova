"""
Volcengine 模型目录测试
"""

import pytest

from nova_ai import VOLCENGINE_MODELS, get_volcengine_model, list_volcengine_models
from nova_ai.types import ModelCost


class TestVolcengineModels:
    """Volcengine 模型数据测试"""

    def test_models_exist(self):
        # 生成目录（scripts/generate_models.py）：models.dev 在售 + 钉住的 260425 对
        assert len(VOLCENGINE_MODELS) == 13
        assert "deepseek-v3-2-251201" not in VOLCENGINE_MODELS  # 已下线
        assert (
            "doubao-seed-1-6-vision-250815" not in VOLCENGINE_MODELS
        )  # Retiring，被过滤
        assert "deepseek-v4-flash-260425" in VOLCENGINE_MODELS  # 钉住
        assert "deepseek-v4-pro-260425" in VOLCENGINE_MODELS
        assert (
            "deepseek-v4-flash-ga-260731" in VOLCENGINE_MODELS
        )  # 新 GA 版（models.dev）

    def test_deepseek_v4_flash_fields(self):
        m = VOLCENGINE_MODELS["deepseek-v4-flash-260425"]
        assert m.id == "deepseek-v4-flash-260425"
        assert m.name == "Deepseek-V4-Flash"
        assert m.reasoning is True
        assert m.cost.input == 1
        assert m.context_window == 1048576
        assert m.max_tokens == 393216

    def test_deepseek_v4_pro_fields(self):
        m = VOLCENGINE_MODELS["deepseek-v4-pro-260425"]
        assert m.id == "deepseek-v4-pro-260425"
        assert m.name == "Deepseek-V4-Pro"
        assert m.reasoning is True
        assert m.cost.input == 12
        assert m.cost.output == 24

    def test_get_volcengine_model_success(self):
        m = get_volcengine_model("deepseek-v4-flash-260425")
        assert m.id == "deepseek-v4-flash-260425"

    def test_get_volcengine_model_not_found(self):
        with pytest.raises(KeyError):
            get_volcengine_model("nonexistent")

    def test_list_volcengine_models(self):
        models = list_volcengine_models()
        assert len(models) == 13
        assert "deepseek-v4-flash-260425" in models

    def test_all_models_have_required_fields(self):
        """所有模型都有必需的字段"""
        for model_id, m in VOLCENGINE_MODELS.items():
            assert m.id
            assert m.name
            assert m.api
            assert m.provider
            assert m.base_url
            assert isinstance(m.reasoning, bool)
            assert m.input_types
            assert isinstance(m.cost, ModelCost)
            assert m.context_window > 0
            assert m.max_tokens > 0
