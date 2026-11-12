"""目录源装载的失败隔离与 strict 分流测试。"""

import pytest
from nova_catalog.sources.load import load_catalog_sources
from nova_catalog.sources.openrouter import (
    OpenRouterDecisionSource,
    OpenRouterImageSource,
)

from tests._helpers import FakeSource, raw_openrouter_decision, raw_openrouter_image


@pytest.mark.asyncio
async def test_source_loading_failure_isolated_by_strictness(capsys):
    class ImageSource(OpenRouterImageSource):
        async def payload(self):
            return [raw_openrouter_image()]

    class FailingImageSource(OpenRouterImageSource):
        async def payload(self):
            raise RuntimeError("images offline")

    class DecisionSource(OpenRouterDecisionSource):
        async def payload(self):
            return [raw_openrouter_decision()]

    class FailingDecisionSource(OpenRouterDecisionSource):
        async def payload(self):
            raise RuntimeError("decisions offline")

    loaded = await load_catalog_sources(
        models_dev_source=FakeSource(error=RuntimeError("offline")),
        openrouter_image_source=ImageSource(),
        openrouter_decision_source=DecisionSource(),
    )
    assert loaded.models_dev == {}
    assert len(loaded.openrouter_images) == 1
    assert len(loaded.openrouter_decisions) == 1
    assert "models.dev source failed" in capsys.readouterr().err

    with pytest.raises(RuntimeError, match="offline"):
        await load_catalog_sources(
            True,
            models_dev_source=FakeSource(error=RuntimeError("offline")),
            openrouter_image_source=ImageSource(),
            openrouter_decision_source=DecisionSource(),
        )
    with pytest.raises(RuntimeError, match="images offline"):
        await load_catalog_sources(
            True,
            models_dev_source=FakeSource(),
            openrouter_image_source=FailingImageSource(),
            openrouter_decision_source=DecisionSource(),
        )
    with pytest.raises(RuntimeError, match="decisions offline"):
        await load_catalog_sources(
            True,
            models_dev_source=FakeSource(),
            openrouter_image_source=ImageSource(),
            openrouter_decision_source=FailingDecisionSource(),
        )
