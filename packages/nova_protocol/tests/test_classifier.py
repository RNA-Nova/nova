"""分类问题、答案与结果的契约往返测试。"""

import pytest
from nova_protocol import (
    ClassifierAnswer,
    ClassifierContext,
    ClassifierQuestion,
    ClassifierResult,
    ClassifierStopReason,
)
from pydantic import TypeAdapter


def test_classifier_context_questions_roundtrip_by_type():
    context = ClassifierContext.model_validate(
        {
            "state": {"text": "deployment succeeded"},
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": "Choose an outcome",
                    "criteria": {"success": "Succeeded", "failure": "Failed"},
                },
                "score": {
                    "type": "score",
                    "instructions": "Score confidence",
                    "criteria": ["low", "high"],
                },
                "approved": {
                    "type": "bool",
                    "instructions": "Is it approved?",
                    "criteria": {"true": "Approved", "false": "Rejected"},
                },
            },
        }
    )
    restored = ClassifierContext.model_validate(context.model_dump())
    assert restored == context
    assert [question.type for question in restored.questions.values()] == [
        "choice",
        "score",
        "bool",
    ]


def test_classifier_result_answers_roundtrip_by_type():
    result = ClassifierResult.model_validate(
        {
            "api": "typesafe-system-one",
            "provider": "openrouter",
            "model": "typesafe/jev-1.14",
            "answers": {
                "category": {
                    "type": "choice",
                    "choice": "success",
                    "probabilities": {"success": 0.9, "failure": 0.1},
                    "confidence": 0.8,
                },
                "score": {"type": "score", "score": 0.67, "confidence": 0.5},
                "approved": {"type": "bool", "probability": 0.95},
            },
            "stop_reason": ClassifierStopReason.STOP,
            "timestamp": 123,
        }
    )
    restored = ClassifierResult.model_validate(result.model_dump())
    assert restored == result
    assert restored.dump_wire()["stopReason"] == "stop"
    assert [answer.type for answer in restored.answers.values()] == [
        "choice",
        "score",
        "bool",
    ]


@pytest.mark.parametrize("union", [ClassifierQuestion, ClassifierAnswer])
def test_classifier_unions_reject_unknown_discriminators(union):
    with pytest.raises(ValueError, match="future"):
        TypeAdapter(union).validate_python({"type": "future"})
