"""Anthropic strict 工具 schema 净化与按工具裁决（对位 pi ``constrained-sampling.ts``）。

strict 是**工具级**契约（不是模型级开关）：

- 工具声明 ``constrained_sampling``（json_schema + prefer/require）才参与；
- prefer + schema 通过净化（``make_strict_json_schema``）→ 启用 strict；
- prefer + 不通过 → 回落非严格（``None``）；
- require + 不通过 / 端点不支持 → 请求期报错（不给模型次优）。

净化规则（顺序即语义）：先砍 strict 不支持的关键字（通用集 + Anthropic
厂商集），再把 object 节点整成 strict 形态（required=全部属性、
additionalProperties=false、可选且不可空属性包 ``anyOf: [prop, null]``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_protocol import Tool

# 通用 strict 不支持键（pi UNSUPPORTED_STRICT_SCHEMA_KEYS）
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

# Anthropic strict 额外拒收键（pi ANTHROPIC_STRICT_UNSUPPORTED_KEYWORDS——
# 文档：structured-outputs#json-schema-limitations）
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


class UnsupportedStrictJsonSchemaError(ValueError):
    """schema 不符合 strict 子集（viability 判定的失败信号——非运行时错误）。"""


def _is_unsupported_keyword(key: str, value: Any) -> bool:
    """Anthropic 版关键字检查（pi ``isAnthropicStrictUnsupportedKeyword``）。"""
    if key in _ANTHROPIC_STRICT_UNSUPPORTED_KEYWORDS:
        return True
    if key == "minItems":
        return value != 0 and value != 1
    if key == "format":
        return (
            not isinstance(value, str) or value not in _ANTHROPIC_STRICT_STRING_FORMATS
        )
    return False


def _is_structured_schema(schema: Any) -> bool:
    """类型含 object/array（anyOf 联合里禁出现）。"""
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
    """schema 是否允许 null（type/const/enum/anyOf 递归）。"""
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


def _make_node_strict(schema: Any) -> None:
    """递归整 strict 形态（原地修改——调用方已预拷贝）。"""
    if not isinstance(schema, dict):
        raise UnsupportedStrictJsonSchemaError("boolean schemas are unsupported")
    for key in _UNSUPPORTED_STRICT_SCHEMA_KEYS:
        if key in schema:
            raise UnsupportedStrictJsonSchemaError(f"{key} schemas are unsupported")
    for key, value in schema.items():
        if _is_unsupported_keyword(key, value):
            raise UnsupportedStrictJsonSchemaError(f"{key}: {value!r} is unsupported")

    any_of = schema.get("anyOf")
    if any_of is not None:
        if not isinstance(any_of, list) or not any_of:
            raise UnsupportedStrictJsonSchemaError(
                "anyOf must contain at least one schema"
            )
        for variant in any_of:
            if _is_structured_schema(variant):
                raise UnsupportedStrictJsonSchemaError(
                    "object and array unions are unsupported"
                )
            _make_node_strict(variant)

    items = schema.get("items")
    if items is not None:
        if isinstance(items, list):
            raise UnsupportedStrictJsonSchemaError("tuple schemas are unsupported")
        _make_node_strict(items)

    is_object_schema = schema.get("type") == "object"
    if "properties" in schema and not is_object_schema:
        raise UnsupportedStrictJsonSchemaError("properties require type object")
    if not is_object_schema:
        return

    additional = schema.get("additionalProperties")
    if additional is not None and additional is not False:
        raise UnsupportedStrictJsonSchemaError(
            "schema-valued or true additionalProperties is unsupported"
        )
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise UnsupportedStrictJsonSchemaError("object properties must be a schema map")
    required_raw = schema.get("required")
    if required_raw is not None and (
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
        _make_node_strict(prop)
        if key not in required and not _schema_allows_null(prop):
            properties[key] = {"anyOf": [prop, {"type": "null"}]}
    schema["required"] = list(properties)
    schema["additionalProperties"] = False


def make_strict_json_schema(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """工具 schema → strict 子集形态（不符合即抛 ``UnsupportedStrictJsonSchemaError``）。

    拷贝后递归净化；根必须是 ``type: "object"``。
    """
    import copy

    cloned = copy.deepcopy(parameters)
    if not isinstance(cloned, dict):
        raise UnsupportedStrictJsonSchemaError("root schema must have type object")
    _make_node_strict(cloned)
    if cloned.get("type") != "object":
        raise UnsupportedStrictJsonSchemaError("root schema must have type object")
    return cloned


def prefers_json_schema_strict_sampling(tool: Tool) -> bool:
    """工具声明了"能 strict 就 strict"（``strict: "prefer"``——pi 同名判定）。"""
    config = tool.constrained_sampling
    return (
        config is not None
        and config.type == "json_schema"
        and config.strict == "prefer"
    )


# Claude（Anthropic API 与 Bedrock）拒绝超限 strict 工具的报文（pi 原表——
# 已观测限额：20 个 strict 工具；跨 strict 工具 16 个可选/联合类型参数）
_STRICT_TOOL_LIMIT_ERROR_MESSAGES = (
    "The compiled grammar is too large",
    "Too many strict tools (",
    "Schemas contains too many parameters with union types (",
)


def is_strict_tool_limit_error_message(message: str) -> bool:
    """provider 错误报文是否为 strict 工具超限（pi 同名判定）。"""
    return any(limit in message for limit in _STRICT_TOOL_LIMIT_ERROR_MESSAGES)


def resolve_json_schema_strict_sampling(
    tool: Tool,
    supports_strict_tools: bool,
) -> Optional[bool]:
    """按工具裁决 strict（对位 pi ``resolveJsonSchemaStrictSampling``）。

    - 未声明 ``constrained_sampling`` → ``None``（不参与 strict）；
    - 端点支持 + 净化通过 → ``True``；
    - prefer + 净化失败 → ``None``（回落非严格）；
    - require + 净化失败 / 端点不支持 → 请求期 ``ValueError``。
    """
    config = tool.constrained_sampling
    if config is None:
        return None

    if supports_strict_tools:
        try:
            make_strict_json_schema(tool.parameters)
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
    "is_strict_tool_limit_error_message",
    "make_strict_json_schema",
    "prefers_json_schema_strict_sampling",
    "resolve_json_schema_strict_sampling",
]
