"""Provider 装配工厂（组合 Provider 单元与刷新策略，无反向局部 import）。"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from nova_protocol import (
    AnyModel,
    AssistantImages,
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    Credential,
    ImageModel,
    ImagesContext,
    Model,
    ProviderAuth,
    ProviderHeaders,
)

from ..auth.resolve import ModelsError
from ..stream_options import ClassifierOptions, ImagesOptions
from ..utils.model_operations import (
    api_value,
    classifier_error_result,
    image_error_result,
)
from .provider import ApiImpl, ClassifiersImpl, ImagesImpl, PersonalizeFn, Provider
from .strategies import (
    MaterializeCatalogFn,
    overlay_refresh_policy,
    personalized_refresh_policy,
)


def create_provider(
    id: str,
    name: str,
    base_url: Optional[str] = None,
    headers: Optional[ProviderHeaders] = None,
    models: Optional[Sequence[AnyModel]] = None,
    api: Optional[ApiImpl] = None,
    images: Optional[ImagesImpl] = None,
    classifiers: Optional[ClassifiersImpl] = None,
    auth: Optional[ProviderAuth] = None,
    filter_models: Optional[
        Callable[[List[Model], Optional[Credential]], List[Model]]
    ] = None,
    catalog_source: Optional[str] = None,
    catalog_materialize: Optional[MaterializeCatalogFn] = None,
    personalize: Optional[PersonalizeFn] = None,
) -> Provider:
    """装配分操作目录、鉴权与按 model.api 分派的协议实现。"""
    provider = Provider(
        id=id,
        name=name,
        base_url=base_url,
        headers=headers,
        auth=auth,
        filter_models=filter_models,
        catalog_source=catalog_source,
        _baseline_models=list(models) if models is not None else [],
        _api_impl=api,
        _images_impl=dict(images) if images is not None else None,
        _classifiers_impl=dict(classifiers) if classifiers is not None else None,
    )
    if images is not None:

        async def _generate_images(
            model: ImageModel,
            context: ImagesContext,
            options: Optional[ImagesOptions] = None,
        ) -> AssistantImages:
            implementation = (provider._images_impl or {}).get(api_value(model.api))
            if implementation is None:
                return image_error_result(
                    model,
                    ModelsError(
                        "provider",
                        f"Provider {id} has no image implementation for {model.api}",
                    ),
                )
            return await implementation.generate_images(model, context, options)

        provider.generate_images = _generate_images
    if classifiers is not None:

        async def _classify(
            model: ClassifierModel,
            context: ClassifierContext,
            options: Optional[ClassifierOptions] = None,
        ) -> ClassifierResult:
            implementation = (provider._classifiers_impl or {}).get(
                api_value(model.api)
            )
            if implementation is None:
                return classifier_error_result(
                    model,
                    ModelsError(
                        "provider",
                        f"Provider {id} has no classifier implementation for {model.api}",
                    ),
                )
            return await implementation.classify(model, context, options)

        provider.classify = _classify
    if personalize is not None:
        # 账户个性化形态（kimi 订阅覆盖 / 火山 ark 筛选）：replace 读取
        provider.refresh_models = personalized_refresh_policy(
            provider, personalize, materialize=catalog_materialize
        )
        provider._dynamic_replaces_baseline = True
    elif catalog_source is not None:
        # 纯站形态（公共事实目录）：六件套治理出生自带
        provider.refresh_models = overlay_refresh_policy(
            provider, materialize=catalog_materialize
        )
    return provider


__all__ = ["create_provider"]
