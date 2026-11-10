"""JSON Schema strict 工具裁决与净化（pi ``api/constrained-sampling.ts`` 对位）。

协议实现共享同一引擎；厂商额外不兼容关键字经 ``is_unsupported_keyword``
注入。prefer 在不兼容时回落，require 在端点或 schema 不支持时请求期报错。
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Dict, Optional

from nova_protocol import Tool

UnsupportedStrictSchemaKeywordCheck = Callable[[str, Any], bool]

_UNSUPPORTED_STRICT_SCHEMA_KEYS = frozenset(
    [
        "$ref",
        "$defs",
        "definitions",
        "allOf",
        "oneOf",
        "patternProperties",
        "dependentSchemas",
        "dependencies",
        "unevaluatedProperties",
        "propertyNames",
        "contains",
        "prefixItems",
        "not",
        "if",
        "then",
        "else",
    ]
)


class UnsupportedStrictJsonSchemaError(ValueError):
    """schema 不符合 strict 子集（viability 判定的失败信号）。"""


def _is_structured_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    raw_type = schema.get("type")
    types = (
        [raw_type]
        if isinstance(raw_type, str)
        else (raw_type if isinstance(raw_type, list) else [])
    )
    return (
        "object" in types
        or "array" in types
        or "properties" in schema
        or "items" in schema
    )


def _schema_allows_null(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    raw_type = schema.get("type")
    if raw_type == "null" or (isinstance(raw_type, list) and "null" in raw_type):
        return True
    if schema.get("const") is None and "const" in schema:
        return True
    enum = schema.get("enum")
    if isinstance(enum, list) and None in enum:
        return True
    any_of = schema.get("anyOf")
    return isinstance(any_of, list) and any(_schema_allows_null(v) for v in any_of)


def _make_node_strict(
    schema: Any,
    is_unsupported_keyword: Optional[UnsupportedStrictSchemaKeywordCheck],
) -> None:
    if not isinstance(schema, dict):
        raise UnsupportedStrictJsonSchemaError("boolean schemas are unsupported")
    for key in _UNSUPPORTED_STRICT_SCHEMA_KEYS:
        if key in schema:
            raise UnsupportedStrictJsonSchemaError(f"{key} schemas are unsupported")
    if is_unsupported_keyword is not None:
        for key, value in schema.items():
            if is_unsupported_keyword(key, value):
                raise UnsupportedStrictJsonSchemaError(
                    f"{key}: {value!r} is unsupported"
                )

    # 显式 null 不是“键缺席”（TS undefined/null 的 Python 对位）：结构键
    # 一旦声明就必须形状正确，否则 prefer 会误判可用、上线后才被端点拒绝。
    if "anyOf" in schema:
        any_of = schema["anyOf"]
        if not isinstance(any_of, list) or not any_of:
            raise UnsupportedStrictJsonSchemaError(
                "anyOf must contain at least one schema"
            )
        for variant in any_of:
            if _is_structured_schema(variant):
                raise UnsupportedStrictJsonSchemaError(
                    "object and array unions are unsupported"
                )
            _make_node_strict(variant, is_unsupported_keyword)

    if "items" in schema:
        items = schema["items"]
        if isinstance(items, list):
            raise UnsupportedStrictJsonSchemaError("tuple schemas are unsupported")
        _make_node_strict(items, is_unsupported_keyword)

    is_object_schema = schema.get("type") == "object"
    if "properties" in schema and not is_object_schema:
        raise UnsupportedStrictJsonSchemaError("properties require type object")
    if not is_object_schema:
        return

    if "additionalProperties" in schema and schema["additionalProperties"] is not False:
        raise UnsupportedStrictJsonSchemaError(
            "schema-valued or true additionalProperties is unsupported"
        )
    properties = schema.get("properties")
    if "properties" in schema and not isinstance(properties, dict):
        raise UnsupportedStrictJsonSchemaError("object properties must be a schema map")
    required_raw = schema.get("required")
    if "required" in schema and (
        not isinstance(required_raw, list)
        or any(not isinstance(key, str) for key in required_raw)
    ):
        raise UnsupportedStrictJsonSchemaError("object required must be a string array")

    properties = properties or {}
    required = set(required_raw or [])
    unknown = required - set(properties)
    if unknown:
        raise UnsupportedStrictJsonSchemaError("required contains an unknown property")
    for key, prop in properties.items():
        _make_node_strict(prop, is_unsupported_keyword)
        if key not in required and not _schema_allows_null(prop):
            properties[key] = {"anyOf": [prop, {"type": "null"}]}
    schema["required"] = list(properties)
    schema["additionalProperties"] = False


def make_strict_json_schema(
    parameters: Dict[str, Any],
    is_unsupported_keyword: Optional[UnsupportedStrictSchemaKeywordCheck] = None,
) -> Dict[str, Any]:
    """拷贝工具 schema 并转换为 strict 子集；根必须是 object。"""
    cloned = copy.deepcopy(parameters)
    if not isinstance(cloned, dict):
        raise UnsupportedStrictJsonSchemaError("root schema must have type object")
    _make_node_strict(cloned, is_unsupported_keyword)
    if cloned.get("type") != "object":
        raise UnsupportedStrictJsonSchemaError("root schema must have type object")
    return cloned


def get_json_schema_tool_parameters(
    tool: Tool, strict: Optional[bool]
) -> Dict[str, Any]:
    """strict 工具返回净化副本，其余保留原 schema。"""
    return (
        make_strict_json_schema(tool.parameters) if strict is True else tool.parameters
    )


def prefers_json_schema_strict_sampling(tool: Tool) -> bool:
    """工具声明了“能 strict 就 strict”（``strict: "prefer"``）。"""
    config = tool.constrained_sampling
    return (
        config is not None
        and config.type == "json_schema"
        and config.strict == "prefer"
    )


def resolve_json_schema_strict_sampling(
    tool: Tool,
    supports_strict_tools: bool,
    is_unsupported_keyword: Optional[UnsupportedStrictSchemaKeywordCheck] = None,
) -> Optional[bool]:
    """按工具声明、端点能力和 schema 兼容性裁决 strict。"""
    config = tool.constrained_sampling
    if config is None:
        return None

    if supports_strict_tools:
        try:
            make_strict_json_schema(tool.parameters, is_unsupported_keyword)
            return True
        except UnsupportedStrictJsonSchemaError as error:
            if config.strict != "require":
                return None
            raise ValueError(
                f'Tool "{tool.name}" requires JSON-schema constrained sampling, '
                f"but {error}."
            ) from error
    if config.strict == "require":
        raise ValueError(
            f'Tool "{tool.name}" requires JSON-schema constrained sampling, '
            "but strict tools are unsupported."
        )
    return None


__all__ = [
    "UnsupportedStrictJsonSchemaError",
    "UnsupportedStrictSchemaKeywordCheck",
    "get_json_schema_tool_parameters",
    "make_strict_json_schema",
    "prefers_json_schema_strict_sampling",
    "resolve_json_schema_strict_sampling",
]
