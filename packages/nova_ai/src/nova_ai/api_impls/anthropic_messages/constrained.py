"""Anthropic 的 strict-schema 限制词表（共享引擎归 ``_shared/constrained.py``）。"""

from __future__ import annotations

from typing import Any, Dict

from .._shared.constrained import UnsupportedStrictJsonSchemaError
from .._shared.constrained import make_strict_json_schema as _make_strict_json_schema
from .._shared.constrained import prefers_json_schema_strict_sampling
from .._shared.constrained import (
    resolve_json_schema_strict_sampling as _resolve_json_schema_strict_sampling,
)

_ANTHROPIC_STRICT_UNSUPPORTED_KEYWORDS = frozenset(
    [
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "maxItems",
        "uniqueItems",
        "minContains",
        "maxContains",
        "minProperties",
        "maxProperties",
    ]
)

_ANTHROPIC_STRICT_STRING_FORMATS = frozenset(
    [
        "date-time",
        "time",
        "date",
        "duration",
        "email",
        "hostname",
        "uri",
        "ipv4",
        "ipv6",
        "uuid",
    ]
)


def _is_unsupported_keyword(key: str, value: Any) -> bool:
    if key in _ANTHROPIC_STRICT_UNSUPPORTED_KEYWORDS:
        return True
    if key == "minItems":
        return value != 0 and value != 1
    if key == "format":
        return (
            not isinstance(value, str) or value not in _ANTHROPIC_STRICT_STRING_FORMATS
        )
    return False


def make_strict_json_schema(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """按 Anthropic 的额外关键字限制调用共享净化引擎。"""
    return _make_strict_json_schema(parameters, _is_unsupported_keyword)


def resolve_json_schema_strict_sampling(tool, supports_strict_tools: bool):
    """按 Anthropic 的限制词表裁决 strict。"""
    return _resolve_json_schema_strict_sampling(
        tool, supports_strict_tools, _is_unsupported_keyword
    )


# Claude（Anthropic API 与 Bedrock）拒绝超限 strict 工具的报文（pi 原表）。
_STRICT_TOOL_LIMIT_ERROR_MESSAGES = (
    "The compiled grammar is too large",
    "Too many strict tools (",
    "Schemas contains too many parameters with union types (",
)


def is_strict_tool_limit_error_message(message: str) -> bool:
    """provider 错误报文是否为 strict 工具超限。"""
    return any(limit in message for limit in _STRICT_TOOL_LIMIT_ERROR_MESSAGES)


__all__ = [
    "UnsupportedStrictJsonSchemaError",
    "is_strict_tool_limit_error_message",
    "make_strict_json_schema",
    "prefers_json_schema_strict_sampling",
    "resolve_json_schema_strict_sampling",
]
