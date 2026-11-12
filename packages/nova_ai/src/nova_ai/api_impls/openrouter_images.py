"""OpenRouter 图像生成：经非流式 Chat Completions 返回文本与图像。"""

from __future__ import annotations

import inspect
import re
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple, cast

from nova_async import AbortScope, await_with_abort
from nova_protocol import (
    AbortedError,
    AssistantImages,
    ImageContent,
    ImageModel,
    ImagesContext,
    ImagesStopReason,
    TextContent,
    Usage,
    is_aborted,
    redact_secrets,
)
from openai import AsyncOpenAI, Omit

from ..stream_options import ImagesOptions, ModelIdentity, ProviderResponse
from ..utils.errors import LoginRequiredError, normalize_error
from ..utils.http import filter_headers_for_hook
from ..utils.model_utils import calculate_cost
from ..utils.surrogate import sanitize_surrogates
from ._shared.openai_request import (
    build_chat_completion_kwargs,
    collect_omitted_headers,
)
from ._shared.retry import RetryOptions, retry_provider_request
from ._shared.timeouts import build_request_timeout
from ._shared.user_agent import get_nova_user_agent

_DATA_IMAGE = re.compile(r"data:([^;]+);base64,(.+)", re.DOTALL)


def _merge_headers(
    headers: Dict[str, Optional[str]], updates: Optional[Mapping[str, Optional[str]]]
) -> None:
    """按 HTTP 头名大小写不敏感语义覆盖；None 保留到 SDK 边界删头。"""
    for key, value in (updates or {}).items():
        lowered = key.lower()
        for existing in list(headers):
            if existing.lower() == lowered:
                del headers[existing]
        headers[key] = value


def _client(model: ImageModel, options: ImagesOptions) -> AsyncOpenAI:
    if not options.api_key:
        raise LoginRequiredError(model.provider)
    headers: Dict[str, Optional[str]] = {"User-Agent": get_nova_user_agent()}
    _merge_headers(headers, model.headers)
    _merge_headers(headers, options.headers)
    return AsyncOpenAI(
        api_key=options.api_key,
        base_url=model.base_url,
        # None 是删头语义；Omit 是 Python SDK 的对应哨兵。
        default_headers=cast(
            Mapping[str, str],
            {key: Omit() if value is None else value for key, value in headers.items()},
        ),
        max_retries=0,
    )


def _params(model: ImageModel, context: ImagesContext) -> Dict[str, Any]:
    content: List[Dict[str, Any]] = []
    for item in context.input:
        if isinstance(item, TextContent):
            content.append({"type": "text", "text": sanitize_surrogates(item.text)})
        else:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.mime_type};base64,{item.data}"},
                }
            )
    return {
        "model": model.id,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "modalities": ["image", "text"] if "text" in model.output_types else ["image"],
    }


def _message_parts(message: Any) -> Tuple[Optional[str], List[Any]]:
    """归一 SDK 未担保残余；当前 openai SDK 实测 extra=allow，images 在 dump 中。"""
    data = message.model_dump()
    images = data.get("images")
    content = data.get("content")
    return (
        content if isinstance(content, str) else None,
        images if isinstance(images, list) else [],
    )


def _usage(response: Any, model: ImageModel) -> Optional[Usage]:
    """将 Chat Completions usage 与缓存明细归一，再按图像目录费率计费。"""
    if response.usage is None:
        return None
    data = response.usage.model_dump()
    details = data.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cache_write = details.get("cache_write_tokens") or 0
    reported_read = details.get("cached_tokens") or 0
    # cached_tokens 与 cache_write_tokens 是独立计数，不能互相抵扣。
    cache_read = reported_read
    prompt = data.get("prompt_tokens") or 0
    output = data.get("completion_tokens") or 0
    input_tokens = max(0, prompt - cache_read - cache_write)
    usage = Usage(
        input=input_tokens,
        output=output,
        cache_read=cache_read,
        cache_write=cache_write,
        total_tokens=input_tokens + output + cache_read + cache_write,
    )
    calculate_cost(model, usage)
    return usage


def _append_response_content(output: AssistantImages, response: Any) -> None:
    if not response.choices:
        return
    content, images = _message_parts(response.choices[0].message)
    if content:
        output.output.append(TextContent(text=content))
    for entry in images:
        if not isinstance(entry, dict):
            continue
        raw_url = entry.get("image_url")
        url = (
            raw_url
            if isinstance(raw_url, str)
            else raw_url.get("url") if isinstance(raw_url, dict) else None
        )
        match = _DATA_IMAGE.fullmatch(url) if isinstance(url, str) else None
        if match is not None:
            output.output.append(
                ImageContent(mime_type=match.group(1), data=match.group(2))
            )


async def generate_images(
    model: ImageModel,
    context: ImagesContext,
    options: Optional[ImagesOptions] = None,
) -> AssistantImages:
    """生成图像；错误/取消以 AssistantImages 终态返回，不向调用方抛错。"""
    options = options or ImagesOptions()
    output = AssistantImages(
        api=model.api,
        provider=model.provider,
        model=model.id,
        output=[],
        stop_reason=ImagesStopReason.STOP,
        timestamp=int(time.time() * 1000),
    )
    client: Optional[AsyncOpenAI] = None
    try:
        if is_aborted(options.signal):
            raise AbortedError("Request aborted")
        client = _client(model, options)
        params = _params(model, context)
        if options.on_payload is not None:
            replacement = options.on_payload(params, ModelIdentity.from_model(model))
            if inspect.isawaitable(replacement):
                replacement = await await_with_abort(replacement, options.signal)
            if replacement is not None:
                params = cast(Dict[str, Any], replacement)

        request_timeout = build_request_timeout(options.timeout)
        signal = options.signal

        async def _request() -> Any:
            request_params = build_chat_completion_kwargs(params)
            omitted_headers = collect_omitted_headers(model.headers, options.headers)
            if omitted_headers:
                request_params["extra_headers"] = {
                    **omitted_headers,
                    **(request_params.get("extra_headers") or {}),
                }
            request_params.setdefault("timeout", request_timeout)
            return await client.chat.completions.with_raw_response.create(
                **request_params
            )

        retry_options: RetryOptions = {
            "max_retries": options.max_retries or 0,
            "signal": signal,
        }
        if options.max_retry_delay_ms is not None:
            retry_options["max_retry_delay_ms"] = options.max_retry_delay_ms
        abort_scope = AbortScope(signal)
        with abort_scope:
            raw_response = await retry_provider_request(_request, retry_options)
            if options.on_response is not None:
                callback = options.on_response(
                    ProviderResponse(
                        status=raw_response.status_code,
                        headers=filter_headers_for_hook(dict(raw_response.headers)),
                    ),
                    ModelIdentity.from_model(model),
                )
                if inspect.isawaitable(callback):
                    await callback
        # scope 会吞掉底层取消并恢复控制流；解析响应前必须把取消落成语义。
        abort_scope.raise_if_aborted()
        response = raw_response.parse()
        output.response_id = response.id
        output.usage = _usage(response, model)
        _append_response_content(output, response)
        return output
    except AbortedError as error:
        output.stop_reason = ImagesStopReason.ABORTED
        output.error_message = redact_secrets(str(error))
        return output
    except Exception as error:
        output.stop_reason = (
            ImagesStopReason.ABORTED
            if is_aborted(options.signal)
            else ImagesStopReason.ERROR
        )
        output.error_message = (
            redact_secrets(str(error))
            if output.stop_reason is ImagesStopReason.ABORTED
            else normalize_error(error).message
        )
        return output
    finally:
        # 清理在 AbortScope 外，避免 level-triggered 取消反复打断关 client。
        if client is not None:
            try:
                await client.close()
            except Exception:
                # 清理失败不覆盖已经生成的结果或原始错误终态。
                pass


__all__ = ["generate_images"]
