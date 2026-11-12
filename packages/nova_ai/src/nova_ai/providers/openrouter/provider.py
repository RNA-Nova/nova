"""OpenRouter 图像与分类 provider 工厂。"""

from typing import cast

from nova_protocol import ProviderAuth

from ...auth.helpers import env_api_key_auth
from ...gateway import Provider, ProviderClassifier, ProviderImages, create_provider
from .models import OPENROUTER_CLASSIFIER_MODELS, OPENROUTER_IMAGE_MODELS

BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_provider() -> Provider:
    """构造 OpenRouter 图像/分类 provider；本批不注册其聊天目录。"""
    from ...api_impls import openrouter_images, typesafe_system_one

    return create_provider(
        id="openrouter",
        name="OpenRouter",
        base_url=BASE_URL,
        models=[
            *OPENROUTER_IMAGE_MODELS.values(),
            *OPENROUTER_CLASSIFIER_MODELS.values(),
        ],
        images={
            "openrouter-images": cast(ProviderImages, openrouter_images),
        },
        classifiers={
            "typesafe-system-one": cast(ProviderClassifier, typesafe_system_one),
        },
        auth=ProviderAuth(
            api_key=env_api_key_auth("OpenRouter API Key", ["OPENROUTER_API_KEY"])
        ),
    )


__all__ = ["BASE_URL", "openrouter_provider"]
