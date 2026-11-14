"""OpenRouter provider 的构建与 strict 传播测试。"""

import pytest
from nova_ai.catalog.build import build_catalog
from nova_ai.catalog.providers.openrouter import DEF as OPENROUTER
from tests.catalog._helpers import (
    catalog_sources,
    raw_openrouter_decision,
    raw_openrouter_image,
)


def test_strict_reaches_openrouter_source_translation():
    source = catalog_sources(
        openrouter_images=[
            raw_openrouter_image(),
            raw_openrouter_image("bad", architecture=[]),
        ]
    )
    with pytest.raises(ValueError, match="architecture"):
        build_catalog([OPENROUTER], source, strict=True)


def test_openrouter_builds_classifier_catalog_separately():
    result = build_catalog(
        [OPENROUTER],
        catalog_sources(
            openrouter_decisions=[
                raw_openrouter_decision(
                    architecture={
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["decisions"],
                    }
                )
            ]
        ),
    )
    catalog = result["openrouter"]
    assert catalog.chat == []
    assert catalog.images == []
    assert len(catalog.classifiers) == 1
    assert catalog.classifiers[0].get("type") == "classifier"
    assert catalog.classifiers[0].get("api") == "typesafe-system-one"
    # 与 pi 一致：分类图片不经过 chat/image 的默认 resize 阶段。
    assert "input_limits" not in catalog.classifiers[0]
