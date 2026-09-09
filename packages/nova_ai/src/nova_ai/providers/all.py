"""
内置 Provider 列表

对齐 TS ``src/providers/all.ts``：集中构造所有内置 provider 实例，并提供
注册到 ``Models`` 集合的便捷函数。
"""

from typing import List, Optional

from ..gateway import Models, ModelsStore, Provider, create_models
from ..types.model import Model
from .kimi_coding.provider import kimi_coding_provider
from .moonshotai.provider import moonshotai_provider
from .moonshotai_cn.provider import moonshotai_cn_provider
from .volcengine.provider import volcengine_provider


def builtin_providers() -> List[Provider]:
    """返回所有内置 provider 的实例列表。"""
    return [
        moonshotai_provider(),
        moonshotai_cn_provider(),
        kimi_coding_provider(),
        volcengine_provider(),
    ]


def builtin_models(models_store: Optional[ModelsStore] = None) -> Models:
    """构造包含所有内置 provider 的 Models 集合。"""
    models = create_models(models_store=models_store)
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
    """基线目录的生成时间（epoch ms；来自 data/.manifest.json，缺失返回 None）。

    作为远程目录物化的新鲜度竞速锚点：基线比运行时缓存新时，缓存 overlay
    让位（对齐 TS ``getBuiltinModelDataGeneratedAt``）。
    """
    import json
    from datetime import datetime
    from pathlib import Path

    manifest_path = Path(__file__).resolve().parent / "data" / ".manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text("utf-8"))
        return int(datetime.fromisoformat(manifest["generatedAt"]).timestamp() * 1000)
    except Exception:
        return None
