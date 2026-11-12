"""OpenRouter 的图像与分类目录构建；本批不构建聊天目录。"""

from typing import List

from nova_protocol import ModelFields

from ..define import ProviderContext, define_provider, stamp_fields
from ..sources.openrouter import (
    OPENROUTER_BASE_URL,
    openrouter_decisions_to_nova,
    openrouter_images_to_nova,
)


def _images(ctx: ProviderContext) -> List[ModelFields]:
    sourced = openrouter_images_to_nova(
        ctx.provider_id, ctx.sources.openrouter_images, strict=ctx.strict
    )
    if ctx.strict and not sourced:
        raise ValueError(
            f"OpenRouter returned no usable image models for {ctx.provider_id}"
        )
    images: List[ModelFields] = []
    for fields in sourced:
        model_id = fields.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(f"{ctx.provider_id}: image model is missing id")
        images.append(
            stamp_fields(
                model_id,
                fields,
                provider_id=ctx.provider_id,
                base_url=OPENROUTER_BASE_URL,
            )
        )
    return images


def _classifiers(ctx: ProviderContext) -> List[ModelFields]:
    sourced = openrouter_decisions_to_nova(
        ctx.provider_id, ctx.sources.openrouter_decisions, strict=ctx.strict
    )
    if ctx.strict and not sourced:
        raise ValueError(
            f"OpenRouter returned no usable decision models for {ctx.provider_id}"
        )
    return sourced


DEF = define_provider(id="openrouter", images=_images, classifiers=_classifiers)
