"""目录词汇（gateway/catalog.py）测试——声明第二真相的漂移守卫。

``ModelFields``/``ModelCostFields`` 是 ``Model``/``ModelCost`` 构造字段的
TypedDict 声明镜像（规则 10：声明不校验，``Model(**fields)`` 在边界一次
校验）。枢纽模型加/改字段时，镜像键集必须同步——本文件机械执法。
"""

from nova_protocol import Model, ModelCost, ModelCostFields, ModelFields


class TestModelFieldsDriftGuard:
    def test_model_fields_keys_subset_of_model(self):
        """ModelFields 的键 ⊆ Model 的构造字段（值级宽严差异——如
        ``input_types`` 声明宽于模型的 Literal——靠边界校验兜，不查这里）。"""
        assert set(ModelFields.__annotations__) <= set(Model.model_fields)

    def test_model_cost_fields_keys_subset_of_model_cost(self):
        assert set(ModelCostFields.__annotations__) <= set(ModelCost.model_fields)
