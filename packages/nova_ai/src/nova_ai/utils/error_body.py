"""
Provider HTTP 错误对象的标准化与格式化。

代理/网关后面的端点可能返回非 2xx 响应，其 body 无法被 provider SDK 折叠进
`error.message`。SDK 错误对象仍携带 HTTP 状态与原始/解析后的 body，但字段名因
SDK 而异（Mistral、`openai`、`@google/genai`、AWS Bedrock 等）。

`normalize_provider_error` 探测这些已知形状并返回结构化对象；
`format_provider_error` 将其组合成便于展示的字符串。
"""

import json
from dataclasses import dataclass
from typing import Any, Optional

MAX_PROVIDER_ERROR_BODY_CHARS = 4000


@dataclass
class NormalizedProviderError:
    """标准化后的 provider 错误信息。"""

    message: str
    status: Optional[int] = None
    body: Optional[str] = None
    message_carries_body: bool = False


def _safe_json_stringify(value: Any) -> str:
    """安全 JSON 序列化，失败时回退到 str()。"""
    try:
        serialized = json.dumps(value)
        return serialized if serialized is not None else str(value)
    except Exception:
        return str(value)


def _is_non_empty_object(value: Any) -> bool:
    return isinstance(value, dict) and len(value) > 0


def _truncate_error_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def _extract_status(error: Any) -> Optional[int]:
    """按已知 SDK 字段顺序提取 HTTP 状态码。"""
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    status = getattr(error, "status", None)
    if isinstance(status, int):
        return status

    metadata = getattr(error, "$metadata", None)
    if metadata is not None:
        http_status_code = getattr(metadata, "httpStatusCode", None)
        if isinstance(http_status_code, int):
            return http_status_code

    response = getattr(error, "$response", None)
    if response is not None:
        response_status_code = getattr(response, "statusCode", None)
        if isinstance(response_status_code, int):
            return response_status_code

    return None


def _pick_body_text(error: Any) -> Optional[str]:
    """按已知 SDK 字段顺序提取错误 body 文本。"""
    body = getattr(error, "body", None)
    if isinstance(body, str):
        return body

    error_body = getattr(error, "error", None)
    if _is_non_empty_object(error_body):
        return _safe_json_stringify(error_body)

    response = getattr(error, "$response", None)
    if response is not None:
        response_body = getattr(response, "body", None)
        if isinstance(response_body, str):
            return response_body
        if _is_non_empty_object(response_body):
            return _safe_json_stringify(response_body)

    return None


def _extract_body(error: Any) -> Optional[str]:
    """提取并截断错误 body。"""
    body_text = _pick_body_text(error)
    if body_text is None:
        return None
    trimmed = body_text.strip()
    if len(trimmed) == 0:
        return None
    return _truncate_error_text(trimmed, MAX_PROVIDER_ERROR_BODY_CHARS)


def normalize_provider_error(error: Any) -> NormalizedProviderError:
    """将任意 provider SDK 错误标准化为结构化对象。"""
    if not isinstance(error, Exception):
        message = _safe_json_stringify(error)
        return NormalizedProviderError(message=message, message_carries_body=False)

    status = _extract_status(error)
    body = _extract_body(error)
    message = str(error)
    message_carries_body = body is None or body in message

    return NormalizedProviderError(
        status=status,
        body=body,
        message=message,
        message_carries_body=message_carries_body,
    )


def format_provider_error(
    norm: NormalizedProviderError, prefix: Optional[str] = None
) -> str:
    """将标准化错误格式化为展示字符串。"""
    if norm.message_carries_body or norm.status is None or norm.body is None:
        if prefix is not None and norm.status is not None:
            return f"{prefix} ({norm.status}): {norm.message}"
        return norm.message

    if prefix is not None:
        return f"{prefix} ({norm.status}): {norm.body}"
    return f"{norm.status}: {norm.body}"
