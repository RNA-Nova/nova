"""内置 Provider 列表与公共目录查询。

集中构造所有内置 provider 实例，并注册到 ModelsManager 集合。
"""

from typing import List, Optional

from nova_protocol import ClassifierModel, ImageModel, Model

from ..gateway import ModelsManager, ModelsStore, Provider, create_models_manager
from .anthropic.provider import anthropic_provider
from .kimi_coding.provider import kimi_coding_provider
from .moonshotai.provider import moonshotai_provider
from .moonshotai_cn.provider import moonshotai_cn_provider
from .openrouter.provider import openrouter_provider
from .volcengine.provider import volcengine_provider


def builtin_providers() -> List[Provider]:
    """返回所有内置 provider 的实例列表。"""
    return [
        anthropic_provider(),
        moonshotai_provider(),
        moonshotai_cn_provider(),
        kimi_coding_provider(),
        openrouter_provider(),
        volcengine_provider(),
    ]


def builtin_models(models_store: Optional[ModelsStore] = None) -> ModelsManager:
    """构造包含所有内置 provider 的 ModelsManager 集合。"""
    models = create_models_manager(models_store=models_store)
    for provider in builtin_providers():
        models.set_provider(provider)
    return models


def get_builtin_models(provider_id: Optional[str] = None) -> List[Model]:
    """返回指定 provider 或所有内置 provider 的模型列表。"""
    return builtin_models().get_models(provider_id)


def get_builtin_model(provider_id: str, model_id: str) -> Optional[Model]:
    """按 provider id + model id 查找内置聊天模型。"""
    return builtin_models().get_model(provider_id, model_id)


def get_builtin_image_models(
    provider_id: Optional[str] = None,
) -> List[ImageModel]:
    """返回内置图像模型。"""
    return [
        model
        for model in builtin_models().get_models_of_type("image", provider_id)
        if isinstance(model, ImageModel)
    ]


def get_builtin_image_model(provider_id: str, model_id: str) -> Optional[ImageModel]:
    """按 provider id + model id 查找内置图像模型。"""
    model = builtin_models().get_model_of_type("image", provider_id, model_id)
    return model if isinstance(model, ImageModel) else None


def get_builtin_classifier_models(
    provider_id: Optional[str] = None,
) -> List[ClassifierModel]:
    """返回内置分类模型。"""
    return [
        model
        for model in builtin_models().get_models_of_type("classifier", provider_id)
        if isinstance(model, ClassifierModel)
    ]


def get_builtin_classifier_model(
    provider_id: str, model_id: str
) -> Optional[ClassifierModel]:
    """按 provider id + model id 查找内置分类模型。"""
    model = builtin_models().get_model_of_type("classifier", provider_id, model_id)
    return model if isinstance(model, ClassifierModel) else None
