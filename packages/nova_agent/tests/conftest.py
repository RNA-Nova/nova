"""共享 fixture。"""

import pytest
from nova_ai import KnownApi, KnownProvider, Model, ModelCost


@pytest.fixture
def dummy_model() -> Model:
    return Model(
        id="mock-model",
        name="Mock Model",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider=KnownProvider.OPENAI,
        base_url="https://example.com",
        max_tokens=4096,
        context_window=8192,
        input_types=["text"],
        reasoning=False,
        cost=ModelCost(input=0, output=0, cache_read=0, cache_write=0),
    )
