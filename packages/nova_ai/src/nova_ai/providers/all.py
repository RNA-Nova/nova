"""
内置 Provider 列表

对齐 TS ``src/providers/all.ts``：集中构造所有内置 provider 实例，并提供
注册到 ``ModelsManager`` 集合的便捷函数。
"""

from typing import List, Optional

from nova_protocol import Model

from ..gateway import ModelsManager, ModelsStore, Provider, create_models_manager
from .anthropic.provider import anthropic_provider
from .kimi_coding.provider import kimi_coding_provider
from .moonshotai.provider import moonshotai_provider
from .moonshotai_cn.provider import moonshotai_cn_provider
from .volcengine.provider import volcengine_provider


def builtin_providers() -> List[Provider]:
    """返回所有内置 provider 的实例列表。"""
    return [
        anthropic_provider(),
        moonshotai_provider(),
        moonshotai_cn_provider(),
        kimi_coding_provider(),
        volcengine_provider(),
    ]


def builtin_models(models_store: Optional[ModelsStore] = None) -> ModelsManager:
    """构造包含所有内置 provider 的 ModelsManager 集合。"""
    models = create_models_manager(models_store=models_store)
    for provider in builtin_providers():
        models.set_provider(provider)
    return models


def get_builtin_providers() -> List[str]:
    """返回所有内置 provider 的 id 列表。"""
    return [p.id for p in builtin_providers()]


def get_builtin_models(provider_id: Optional[str] = None) -> List[Model]:
    """返回指定 provider 或所有内置 provider 的模型列表。"""
    return builtin_models().get_models(provider_id)


def get_builtin_model(provider_id: str, model_id: str) -> Optional[Model]:
    """按 provider id + model id 查找内置模型。"""
    return builtin_models().get_model(provider_id, model_id)


def get_builtin_model_data_generated_at() -> Optional[int]:
    """基线目录的生成时间（epoch ms；缺失返回 None）。

    实现归 ``model_catalog.py`` 的 ``builtin_catalog_generated_at``——
    远程目录物化的新鲜度竞速锚点。
    """
    from ..model_catalog import builtin_catalog_generated_at

    return builtin_catalog_generated_at()
