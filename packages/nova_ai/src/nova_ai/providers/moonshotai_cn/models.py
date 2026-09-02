"""Moonshot AI CN 模型定义。

目录与 ``moonshotai/models.py`` 完全一致（对齐 TS
``providers/moonshotai-cn.models.ts``），仅 provider id 与 base_url
不同（``https://api.moonshot.cn/v1``）。直接从 moonshotai 目录派生，
避免两份静态数据漂移。
"""

from typing import Dict

from ...types.enums import KnownProvider
from ...types.model import Model
from ..moonshotai.models import MOONSHOTAI_MODELS

MOONSHOTAI_CN_BASE_URL = "https://api.moonshot.cn/v1"

MOONSHOTAI_CN_MODELS: Dict[str, Model] = {
    model_id: model.model_copy(
        update={
            "provider": KnownProvider.MOONSHOTAI_CN,
            "base_url": MOONSHOTAI_CN_BASE_URL,
        }
    )
    for model_id, model in MOONSHOTAI_MODELS.items()
}


def get_moonshotai_cn_model(model_id: str) -> Model:
    """通过 ID 获取 Moonshot AI CN 模型。"""
    if model_id not in MOONSHOTAI_CN_MODELS:
        raise KeyError(f"Moonshot AI CN model not found: {model_id}")
    return MOONSHOTAI_CN_MODELS[model_id]


def list_moonshotai_cn_models() -> Dict[str, Model]:
    """列出所有 Moonshot AI CN 模型。"""
    return MOONSHOTAI_CN_MODELS.copy()
