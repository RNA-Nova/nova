"""System One 分类协议的 HTTP 级行为测试。"""

import asyncio
import json

import httpx
import pytest
import respx
from nova_protocol import (
    AbortController,
    ClassifierContext,
    ClassifierModel,
    ClassifierStopReason,
    ImageContent,
    ModelCost,
    SystemOneCompat,
)

from nova_ai.api_impls.typesafe_system_one import classify
from nova_ai.stream_options import ClassifierOptions

pytestmark = pytest.mark.asyncio
_URL = "https://openrouter.ai/api/v1/systemone"


def _model(*, image_input: bool = False) -> ClassifierModel:
    return ClassifierModel(
        id="typesafe/jev-1.14",
        name="Jev",
        type="classifier",
        api="typesafe-system-one",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        input_types=["text", "image"] if image_input else ["text"],
        cost=ModelCost(input=0.042, output=0, cache_read=0, cache_write=0),
        context_window=64000,
        compat=SystemOneCompat(
            image_input="state-image-urls", send_image_opt_in_header=False
        ),
    )


def _context() -> ClassifierContext:
    return ClassifierContext.model_validate(
        {
            "state": {"text": "The deployment succeeded."},
            "questions": {
                "category": {
                    "type": "choice",
                    "instructions": "Choose an outcome",
                    "criteria": {"success": "Succeeded", "failure": "Failed"},
                },
                "score": {
                    "type": "score",
                    "instructions": "Score the outcome",
                    "criteria": ["bad", "good"],
                },
                "approved": {
                    "type": "bool",
                    "instructions": "Is it approved?",
                    "criteria": {"true": "Yes", "false": "No"},
                },
            },
        }
    )


def _answers() -> dict:
    return {
        "category": {
            "type": "choice",
            "choice": "success",
            "probabilities": {"success": 0.9, "failure": 0.1},
            "confidence": 0.8,
        },
        "score": {"type": "score", "score": 0.67, "confidence": 0.5},
        "approved": {"type": "noul", "noul": 0.95},
    }


@respx.mock
async def test_choice_score_bool_and_usage_cross_http_boundary():
    route = respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": _answers(),
                "usage": {"input_tokens": 308, "output_tokens": 23},
            },
        )
    )
    result = await classify(
        _model(), _context(), ClassifierOptions(api_key="secret", temperature=1.5)
    )
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body["model"] == "typesafe/jev-1.14"
    assert body["questions"]["approved"]["type"] == "noul"
    assert "temperature" not in body
    assert request.headers["authorization"] == "Bearer secret"
    assert result.stop_reason is ClassifierStopReason.STOP
    approved = result.answers["approved"]
    category = result.answers["category"]
    score = result.answers["score"]
    assert approved.type == "bool" and approved.probability == 0.95
    assert category.type == "choice" and category.choice == "success"
    assert score.type == "score" and score.score == 0.67
    assert result.usage is not None
    assert result.usage.total_tokens == 331
    assert result.usage.cost.total == pytest.approx(308 * 0.042 / 1_000_000)


@respx.mock
async def test_openrouter_images_are_image_url_parts_before_state():
    route = respx.post(_URL).mock(
        return_value=httpx.Response(200, json={"answers": _answers()})
    )
    context = _context().model_copy(
        update={
            "images": [
                ImageContent(mime_type="image/png", data="cG5n"),
            ]
        }
    )
    result = await classify(
        _model(image_input=True), context, ClassifierOptions(api_key="secret")
    )
    body = json.loads(route.calls[0].request.content)
    assert body["state"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5n"}},
        context.state,
    ]
    assert "typesafe-danger-zone-images" not in route.calls[0].request.headers
    assert result.stop_reason is ClassifierStopReason.STOP


@respx.mock(assert_all_called=False)
async def test_text_only_model_rejects_images_without_request():
    route = respx.post(_URL)
    context = _context().model_copy(
        update={"images": [ImageContent(mime_type="image/png", data="cG5n")]}
    )
    result = await classify(_model(), context, ClassifierOptions(api_key="secret"))
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "does not accept image input" in (result.error_message or "")
    assert route.call_count == 0


@respx.mock
async def test_malformed_answer_keeps_billed_usage():
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "answers": {},
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
        )
    )
    result = await classify(_model(), _context(), ClassifierOptions(api_key="secret"))
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert result.answers == {}
    assert "did not return an answer for category" in (result.error_message or "")
    assert result.usage is not None and result.usage.total_tokens == 12


@respx.mock
@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"answers": {"category": {"type": "score"}}},
        {
            "answers": {
                "category": {
                    "type": "choice",
                    "choice": "success",
                    "probabilities": {"success": float("nan")},
                    "confidence": 0.8,
                }
            }
        },
    ],
)
async def test_non_object_type_mismatch_and_non_finite_answer_fail(payload):
    respx.post(_URL).mock(
        return_value=httpx.Response(200, content=json.dumps(payload).encode())
    )
    result = await classify(_model(), _context(), ClassifierOptions(api_key="secret"))
    assert result.stop_reason is ClassifierStopReason.ERROR


@respx.mock
async def test_provider_error_and_retry_share_normalizer_and_retry_engine():
    route = respx.post(_URL).mock(
        side_effect=[
            httpx.Response(500, json={"error": "temporary"}),
            httpx.Response(200, json={"answers": _answers()}),
        ]
    )
    result = await classify(
        _model(),
        _context(),
        ClassifierOptions(api_key="secret", max_retries=1, max_retry_delay_ms=0),
    )
    assert result.stop_reason is ClassifierStopReason.STOP
    assert route.call_count == 2


@respx.mock
async def test_retry_after_and_should_retry_headers_cross_error_boundary():
    limited = respx.post(_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after-ms": "1000"})
    )
    result = await classify(
        _model(),
        _context(),
        ClassifierOptions(api_key="secret", max_retries=1, max_retry_delay_ms=1),
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert limited.call_count == 1

    respx.clear()
    forbidden = respx.post(_URL).mock(
        return_value=httpx.Response(500, headers={"x-should-retry": "false"})
    )
    result = await classify(
        _model(),
        _context(),
        ClassifierOptions(api_key="secret", max_retries=1, max_retry_delay_ms=0),
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert forbidden.call_count == 1


@respx.mock
async def test_response_hook_failure_never_repeats_successful_request():
    class HookError(Exception):
        status_code = 500

    def on_response(*_args):
        raise HookError("observer failed")

    route = respx.post(_URL).mock(
        return_value=httpx.Response(200, json={"answers": _answers()})
    )
    result = await classify(
        _model(),
        _context(),
        ClassifierOptions(
            api_key="secret",
            max_retries=1,
            max_retry_delay_ms=0,
            on_response=on_response,
        ),
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert route.call_count == 1


@respx.mock
async def test_http_error_is_a_result_not_an_exception():
    respx.post(_URL).mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    result = await classify(
        _model(), _context(), ClassifierOptions(api_key="bad", max_retries=0)
    )
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "401" in (result.error_message or "")


@respx.mock(assert_all_called=False)
async def test_pre_aborted_request_returns_aborted_without_network():
    route = respx.post(_URL)
    controller = AbortController(name="test")
    controller.abort()
    result = await classify(
        _model(),
        _context(),
        ClassifierOptions(api_key="secret", signal=controller.signal),
    )
    assert result.stop_reason is ClassifierStopReason.ABORTED
    assert route.call_count == 0


async def test_abort_interrupts_in_flight_classifier_request(monkeypatch):
    started = asyncio.Event()
    calls = 0

    async def hold(_request):
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json={"answers": _answers()})

    client = httpx.AsyncClient(transport=httpx.MockTransport(hold))
    monkeypatch.setattr("nova_ai.utils.http.httpx.AsyncClient", lambda: client)
    controller = AbortController(name="test")
    task = asyncio.create_task(
        classify(
            _model(),
            _context(),
            ClassifierOptions(api_key="secret", signal=controller.signal),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, 1)
    assert result.stop_reason is ClassifierStopReason.ABORTED
    assert calls == 1


@respx.mock(assert_all_called=False)
async def test_abort_inside_async_classifier_hooks():
    controller = AbortController(name="test")
    started = asyncio.Event()

    async def on_payload(_payload, _identity):
        started.set()
        await asyncio.Event().wait()

    route = respx.post(_URL)
    task = asyncio.create_task(
        classify(
            _model(),
            _context(),
            ClassifierOptions(
                api_key="secret", signal=controller.signal, on_payload=on_payload
            ),
        )
    )
    await started.wait()
    controller.abort()
    result = await asyncio.wait_for(task, 1)
    assert result.stop_reason is ClassifierStopReason.ABORTED
    assert route.call_count == 0


@respx.mock(assert_all_called=False)
async def test_other_classifier_api_is_rejected_without_network():
    route = respx.post(_URL)
    model = _model().model_copy(update={"api": "openai-decisions"})
    result = await classify(model, _context(), ClassifierOptions(api_key="secret"))
    assert result.stop_reason is ClassifierStopReason.ERROR
    assert "Unsupported classifier API" in (result.error_message or "")
    assert route.call_count == 0


@respx.mock
async def test_payload_response_hooks_and_header_suppression():
    payloads = []
    responses = []
    route = respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={"answers": _answers()},
            headers={"set-cookie": "sensitive", "x-public": "ok"},
        )
    )

    def on_payload(payload, _identity):
        payloads.append(payload)
        return None

    def on_response(response, _identity):
        responses.append(response)

    model = _model().model_copy(
        update={"headers": {"X-Model": "yes", "X-Remove": "gone"}}
    )
    result = await classify(
        model,
        _context(),
        ClassifierOptions(
            api_key="secret",
            headers={"X-Remove": None},
            on_payload=on_payload,
            on_response=on_response,
        ),
    )
    assert result.stop_reason is ClassifierStopReason.STOP
    assert len(payloads) == 1 and payloads[0]["model"] == model.id
    assert len(responses) == 1 and responses[0].status == 200
    assert "set-cookie" not in responses[0].headers
    assert responses[0].headers["x-public"] == "ok"
    request = route.calls[0].request
    assert request.headers["x-model"] == "yes"
    assert "x-remove" not in request.headers
