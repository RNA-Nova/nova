"""目录构建源的装载与原始数据容器。"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models_dev import ModelsDevSource
from .openrouter import OpenRouterDecisionSource, OpenRouterImageSource


@dataclass(frozen=True)
class CatalogSources:
    """一次纯构建使用的全部原始数据；不含网络或构建状态。"""

    models_dev: Dict[str, Any] = field(default_factory=dict)
    openrouter_images: List[Dict[str, Any]] = field(default_factory=list)
    openrouter_decisions: List[Dict[str, Any]] = field(default_factory=list)


async def load_catalog_sources(
    strict: bool = False,
    *,
    models_dev_source: Optional[ModelsDevSource] = None,
    openrouter_image_source: Optional[OpenRouterImageSource] = None,
    openrouter_decision_source: Optional[OpenRouterDecisionSource] = None,
) -> CatalogSources:
    """分别装载来源；strict 失败即抛，非 strict 诊断后降级失败来源。"""
    models_dev_source = models_dev_source or ModelsDevSource()
    openrouter_image_source = openrouter_image_source or OpenRouterImageSource()
    openrouter_decision_source = (
        openrouter_decision_source or OpenRouterDecisionSource()
    )
    try:
        models_dev = await models_dev_source.payload()
    except Exception as error:
        if strict:
            raise
        print(f"models.dev source failed; using no models: {error}", file=sys.stderr)
        models_dev = {}
    try:
        openrouter_images = await openrouter_image_source.payload()
    except Exception as error:
        if strict:
            raise
        print(
            f"OpenRouter image source failed; using no images: {error}",
            file=sys.stderr,
        )
        openrouter_images = []
    try:
        openrouter_decisions = await openrouter_decision_source.payload()
    except Exception as error:
        if strict:
            raise
        print(
            f"OpenRouter decision source failed; using no classifiers: {error}",
            file=sys.stderr,
        )
        openrouter_decisions = []
    return CatalogSources(
        models_dev=models_dev,
        openrouter_images=openrouter_images,
        openrouter_decisions=openrouter_decisions,
    )


__all__ = ["CatalogSources", "load_catalog_sources"]
