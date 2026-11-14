"""ModelsManager 分类目录、鉴权与分派测试。"""

import pytest
from nova_ai import ClassifierOptions, ModelsManager, create_provider, env_api_key_auth
from nova_ai.auth import ProviderAuth
from nova_protocol import (
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    ClassifierStopReason,
    ImageContent,
    ModelCost,
)

pytestmark = pytest.mark.asyncio


def _model(model_id: str = "decision") -> ClassifierModel:
    return ClassifierModel(
        id=model_id,
        name=model_id,
        type="classifier",
        api="test-classifier",
        provider="test",
        base_url="https://example.test/v1",
        input_types=["text"],
        cost=ModelCost(),
        context_window=1000,
    )


def _context() -> ClassifierContext:
    return ClassifierContext.model_validate(
        {
            "state": {"text": "yes"},
            "questions": {
                "approved": {
                    "type": "bool",
                    "instructions": "Is this approved?",
                    "criteria": {"true": "Yes", "false": "No"},
                }
            },
        }
    )


class _Classifier:
    def __init__(self):
        self.calls = []

    async def classify(self, model, context, options=None):
        self.calls.append((model, context, options))
        return ClassifierResult.model_validate(
            {
                "api": model.api,
                "provider": model.provider,
                "model": model.id,
                "answers": {"approved": {"type": "bool", "probability": 0.9}},
                "stop_reason": ClassifierStopReason.STOP,
            }
        )


def _manager(classifier: _Classifier) -> ModelsManager:
    provider = create_provider(
        id="test",
        name="Test",
        models=[_model()],
        classifiers={"test-classifier": classifier},
        auth=ProviderAuth(
            api_key=env_api_key_auth("Test key", ["TEST_CLASSIFIER_API_KEY"])
        ),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    return manager


async def test_classifier_catalog_auth_and_dispatch():
    classifier = _Classifier()
    manager = _manager(classifier)
    model = manager.get_model_of_type("classifier", "test", "decision")
    assert model is not None
    assert manager.get_model("test", "decision") is None
    result = await manager.classify(
        model, _context(), ClassifierOptions(api_key="secret")
    )
    answer = result.answers["approved"]
    assert answer.type == "bool" and answer.probability == 0.9
    assert len(classifier.calls) == 1
    assert classifier.calls[0][2].api_key == "secret"


async def test_classifier_rejects_chat_model_as_error_result():
    manager = _manager(_Classifier())
    from nova_protocol import KnownApi, Model

    chat = Model(
        id="chat",
        name="Chat",
        api=KnownApi.OPENAI_COMPLETIONS,
        provider="test",
        base_url="https://example.test/v1",
        reasoning=False,
        input_types=["text"],
        cost=ModelCost(),
        context_window=1000,
        max_tokens=100,
    )
    result = await manager.classify(chat, _context())
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "is not a classifier model" in (result.error_message or "")


async def test_classifier_rejects_unsupported_images_before_dispatch():
    classifier = _Classifier()
    manager = _manager(classifier)
    context = _context().model_copy(
        update={"images": [ImageContent(mime_type="image/png", data="cG5n")]}
    )
    result = await manager.classify(
        _model(), context, ClassifierOptions(api_key="secret")
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "does not accept image input" in (result.error_message or "")
    assert classifier.calls == []


async def test_missing_classifier_implementation_is_error_result():
    classifier = _Classifier()
    provider = create_provider(
        id="test",
        name="Test",
        models=[_model()],
        classifiers={"other-api": classifier},
        auth=ProviderAuth(
            api_key=env_api_key_auth("Test key", ["TEST_CLASSIFIER_API_KEY"])
        ),
    )
    manager = ModelsManager()
    manager.set_provider(provider)
    result = await manager.classify(
        _model(), _context(), ClassifierOptions(api_key="secret")
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "has no classifier implementation" in (result.error_message or "")
    assert classifier.calls == []
