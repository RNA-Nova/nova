"""TypeSafe 兼容的 System One 分类协议（本批服务 OpenRouter）。

公共 ``bool`` 问题在线上映射为 ``noul``；回答再映射回 ``bool``。
图像按 OpenRouter 要求作为 ``image_url`` 块置于 state 之前。协议不流式、
不产生聊天消息，失败与取消都以 ``ClassifierResult`` 返回。
"""

from __future__ import annotations

import inspect
import math
import time
from typing import Any, Dict, Mapping, Optional

from nova_ai.utils.sanitize import REDACTED_SECRET, redact_secrets
from nova_async import AbortedError, await_with_abort, is_aborted
from nova_protocol import (
    ClassifierAnswer,
    ClassifierBoolAnswer,
    ClassifierChoiceAnswer,
    ClassifierContext,
    ClassifierModel,
    ClassifierResult,
    ClassifierScoreAnswer,
    ClassifierStopReason,
    KnownClassifierApi,
    Usage,
)

from ..stream_options import ClassifierOptions, ModelIdentity, ProviderResponse
from ..utils.errors import LoginRequiredError, normalize_error
from ..utils.http import HttpFetchError, filter_headers_for_hook, request_with_abort
from ..utils.model_operations import api_value, assert_classifier_input_supported
from ..utils.model_utils import calculate_cost
from ._shared.identity import get_identity_headers
from ._shared.retry import RetryOptions, retry_provider_request
from ._shared.timeouts import build_request_timeout

api = KnownClassifierApi.TYPESAFE_SYSTEM_ONE
_IMAGE_OPT_IN_HEADER = "TypeSafe-Danger-Zone-Images"


def _merge_headers(
    headers: Dict[str, Optional[str]],
    updates: Optional[Mapping[str, Optional[str]]],
) -> None:
    """按 HTTP 头名大小写不敏感覆盖；None 表示 suppress。"""
    for key, value in (updates or {}).items():
        lowered = key.lower()
        for existing in list(headers):
            if existing.lower() == lowered:
                del headers[existing]
        headers[key] = value


def _request_headers(
    model: ClassifierModel, options: ClassifierOptions
) -> Dict[str, str]:
    if not options.api_key:
        raise LoginRequiredError(model.provider)
    headers: Dict[str, Optional[str]] = {
        "authorization": f"Bearer {options.api_key}",
        "content-type": "application/json",
        **get_identity_headers(model, options.identity),
    }
    _merge_headers(headers, model.headers)
    _merge_headers(headers, options.headers)
    return {key: value for key, value in headers.items() if value is not None}


def _wire_questions(context: ClassifierContext) -> Dict[str, Dict[str, Any]]:
    """公共问题模型 → System One 线上字段（bool → noul）。"""
    questions: Dict[str, Dict[str, Any]] = {}
    for question_id, question in context.questions.items():
        payload = question.model_dump()
        if question.type == "bool":
            payload["type"] = "noul"
        questions[question_id] = payload
    return questions


def _request_body(model: ClassifierModel, context: ClassifierContext) -> Dict[str, Any]:
    state = context.state
    if context.images:
        compat = model.compat
        image_input = compat.image_input if compat is not None else None
        if image_input not in (None, "state-image-urls"):
            raise ValueError(
                f"System One API image input {image_input} is not supported by this OpenRouter implementation"
            )
        state = [
            *(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.mime_type};base64,{image.data}"},
                }
                for image in context.images
            ),
            state,
        ]
    return {
        "model": model.id,
        "state": state,
        "questions": _wire_questions(context),
    }


def _required_number(value: Any, label: str) -> float:
    """第三方响应数值边界；bool 与非有限数都不是合法数值。"""
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise ValueError(f"System One API returned an invalid {label}")
    return float(value)


def _parse_usage(value: Any, model: ClassifierModel) -> Optional[Usage]:
    """解析 token 用量；缺席/畸形用量不拖垮已返回的分类答案。"""
    if not isinstance(value, dict) or (
        "input_tokens" not in value and "output_tokens" not in value
    ):
        return None

    def tokens(key: str) -> int:
        count = value.get(key)
        return (
            count
            if isinstance(count, int) and not isinstance(count, bool) and count > 0
            else 0
        )

    input_tokens = tokens("input_tokens")
    output_tokens = tokens("output_tokens")
    usage = Usage(
        input=input_tokens,
        output=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )
    calculate_cost(model, usage)
    return usage


def _parse_answers(
    body: Dict[str, Any], context: ClassifierContext
) -> Dict[str, ClassifierAnswer]:
    """按请求问题逐 ID 验收回答；缺失或类型错配即响亮失败。"""
    raw_answers = body.get("answers")
    if not isinstance(raw_answers, dict):
        raise ValueError("System One API returned an unexpected response")
    answers: Dict[str, ClassifierAnswer] = {}
    for question_id, question in context.questions.items():
        answer = raw_answers.get(question_id)
        if not isinstance(answer, dict):
            raise ValueError(
                f"System One API did not return an answer for {question_id}"
            )
        if question.type == "choice":
            if answer.get("type") != "choice" or not isinstance(
                answer.get("choice"), str
            ):
                raise ValueError(
                    f"System One API did not return a choice answer for {question_id}"
                )
            raw_probabilities = answer.get("probabilities")
            if not isinstance(raw_probabilities, dict):
                raise ValueError(
                    f"System One API returned invalid probabilities for {question_id}"
                )
            probabilities = {
                key: _required_number(value, f"probability for {question_id}.{key}")
                for key, value in raw_probabilities.items()
            }
            answers[question_id] = ClassifierChoiceAnswer(
                choice=answer["choice"],
                probabilities=probabilities,
                confidence=_required_number(
                    answer.get("confidence"), f"confidence for {question_id}"
                ),
            )
        elif question.type == "score":
            if answer.get("type") != "score":
                raise ValueError(
                    f"System One API did not return a score answer for {question_id}"
                )
            answers[question_id] = ClassifierScoreAnswer(
                score=_required_number(answer.get("score"), f"score for {question_id}"),
                confidence=_required_number(
                    answer.get("confidence"), f"confidence for {question_id}"
                ),
            )
        else:
            if answer.get("type") != "noul":
                raise ValueError(
                    f"System One API did not return a bool answer for {question_id}"
                )
            answers[question_id] = ClassifierBoolAnswer(
                probability=_required_number(
                    answer.get("noul"), f"probability for {question_id}"
                )
            )
    return answers


async def classify(
    model: ClassifierModel,
    context: ClassifierContext,
    options: Optional[ClassifierOptions] = None,
) -> ClassifierResult:
    """执行一次 System One 分类；任何失败均编码进返回结果。"""
    options = options or ClassifierOptions()
    output = ClassifierResult(
        api=model.api,
        provider=model.provider,
        model=model.id,
        answers={},
        stop_reason=ClassifierStopReason.STOP,
        timestamp=int(time.time() * 1000),
    )
    try:
        if api_value(model.api) != api.value:
            raise ValueError(f"Unsupported classifier API: {model.api}")
        assert_classifier_input_supported(model, context)
        headers = _request_headers(model, options)
        body = _request_body(model, context)
        if (
            context.images
            and model.compat is not None
            and model.compat.send_image_opt_in_header
            and not any(
                key.lower() == _IMAGE_OPT_IN_HEADER.lower()
                for key in (options.headers or {})
            )
        ):
            headers[_IMAGE_OPT_IN_HEADER] = "true"
        if options.on_payload is not None:
            replacement = options.on_payload(body, ModelIdentity.from_model(model))
            if inspect.isawaitable(replacement):
                replacement = await await_with_abort(replacement, options.signal)
            if replacement is not None:
                if not isinstance(replacement, dict):
                    raise TypeError("on_payload must return a request object or None")
                body = replacement

        async def _request() -> tuple[Dict[str, Any], ProviderResponse]:
            response = await request_with_abort(
                "POST",
                f"{model.base_url.rstrip('/')}/systemone",
                headers=headers,
                json_body=body,
                timeout=build_request_timeout(options.timeout),
                signal=options.signal,
            )
            if response.status_code != 200:
                raise HttpFetchError(
                    f"System One API returned {response.status_code}: {response.text}",
                    status_code=response.status_code,
                    headers=dict(response.headers),
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("System One API returned a non-object response")
            return payload, ProviderResponse(
                status=response.status_code,
                headers=filter_headers_for_hook(dict(response.headers)),
            )

        retry_options: RetryOptions = {
            "max_retries": (
                options.max_retries if options.max_retries is not None else 0
            ),
            "signal": options.signal,
        }
        if options.max_retry_delay_ms is not None:
            retry_options["max_retry_delay_ms"] = options.max_retry_delay_ms
        payload, provider_response = await retry_provider_request(
            _request, retry_options
        )
        # 观察钩子不属于可重试请求：成功响应只通知一次，钩子异常不得导致
        # 已执行且可能已计费的分类请求再次发送（pi postClassifierRequest 同款）。
        if options.on_response is not None:
            callback = options.on_response(
                provider_response, ModelIdentity.from_model(model)
            )
            if inspect.isawaitable(callback):
                await await_with_abort(callback, options.signal)
        # 先保留用量：响应回答畸形时，本次请求仍可能已经被计费。
        output.usage = _parse_usage(payload.get("usage"), model)
        output.answers = _parse_answers(payload, context)
        return output
    except AbortedError as error:
        output.stop_reason = ClassifierStopReason.ABORTED
        output.error_message = redact_secrets(str(error))
        return output
    except Exception as error:
        output.stop_reason = (
            ClassifierStopReason.ABORTED
            if is_aborted(options.signal)
            else ClassifierStopReason.ERROR
        )
        output.error_message = (
            redact_secrets(str(error))
            if output.stop_reason is ClassifierStopReason.ABORTED
            else normalize_error(
                error, domain="classifier", provider=model.provider
            ).message
        )
        return output


__all__ = ["api", "classify"]
