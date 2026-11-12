"""OpenRouter 分类 e2e：目录 → 鉴权 → System One 结构化答案。"""

import asyncio

import pytest
from nova_protocol import ClassifierContext, ClassifierModel, ClassifierStopReason

from nova_ai.stream_options import ClassifierOptions

from ._helpers.auth import build_manager, has_credential

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.asyncio,
    pytest.mark.timeout(300),
]

_MODEL_ID = "parallel/decisions-fast:free"


async def test_openrouter_returns_classifier_bool_answer():
    if not has_credential("openrouter"):
        pytest.skip("OpenRouter credentials are not configured")
    manager = await build_manager()
    model = manager.get_model_of_type("classifier", "openrouter", _MODEL_ID)
    if model is None:
        pytest.skip(f"Current classifier catalog does not contain {_MODEL_ID}")
    assert isinstance(model, ClassifierModel)
    context = ClassifierContext.model_validate(
        {
            "state": "The customer was charged twice for the same order.",
            "questions": {
                "is_billing": {
                    "type": "bool",
                    "instructions": "Is this a billing issue?",
                    "criteria": {
                        "true": "Billing or duplicate charge",
                        "false": "Unrelated issue",
                    },
                }
            },
        }
    )
    async with asyncio.timeout(120):
        result = await manager.classify(
            model, context, ClassifierOptions(timeout=60, max_retries=0)
        )
    assert result.stop_reason is ClassifierStopReason.STOP, result.error_message
    answer = result.answers["is_billing"]
    assert answer.type == "bool"
    assert 0 <= answer.probability <= 1
    assert result.usage is not None
    assert result.usage.total_tokens > 0
