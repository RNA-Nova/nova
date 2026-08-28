"""
工具调用验证器

按 JSON Schema 校验并矫正工具调用参数。流程与 TS pi-ai ``utils/validation.ts`` 对齐：
deepcopy → coerce（类型矫正）→ validate（jsonschema 校验）。

不缓存 validator：jsonschema 是解释执行，``Draft7Validator`` 构造约 2µs，
与生成缓存 key 的 ``json.dumps`` 开销相当，缓存没有净收益。
"""

import copy
import inspect
import json
import math
from typing import Any, Callable, Dict, List, Union

import jsonschema
from nova_ai import Message, Tool, ToolCall

from .types.base import AgentMessage


def default_convert_to_llm(messages: List[AgentMessage]) -> List[Message]:
    """Default converter: keep only LLM-compatible messages.

    用 getattr 而不是直接访问 ``.role``：CustomAgentMessage 可以不带 role 字段，
    此时应被过滤掉而不是抛 AttributeError（与 TS defaultConvertToLlm 对齐）。
    """
    return [
        m
        for m in messages
        if getattr(m, "role", None) in ("user", "assistant", "toolResult")
    ]


async def invoke_hook(hook: Callable[..., Any], *args: Any, default: Any = None) -> Any:
    """调用一个可同步可异步的 hook；hook 为 None 时返回 default。

    仓库内所有 hook 调用点统一走这里，避免"判空 → 调用 → isawaitable → 条件 await"
    的样板在多处复制。
    """
    if hook is None:
        return default
    result = hook(*args)
    if inspect.isawaitable(result):
        return await result
    return result


# ----------------------------------------------------------------------
# JSON Schema 类型矫正（对齐 TS coerceWithJsonSchema）
# ----------------------------------------------------------------------


def _get_schema_types(schema: Dict[str, Any]) -> List[str]:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return [schema_type]
    if isinstance(schema_type, list):
        return [t for t in schema_type if isinstance(t, str)]
    return []


def _matches_json_type(value: Any, schema_type: str) -> bool:
    """值是否已匹配目标 JSON 类型（判定口径与 jsonschema 校验一致）。

    Python 特有处理：``bool`` 是 ``int`` 的子类，number/integer 必须显式排除 bool。
    """
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "null":
        return value is None
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return False


def _coerce_primitive(value: Any, schema_type: str) -> Any:
    """单类型矫正，逐条对齐 TS coercePrimitiveByType。"""
    if schema_type == "number":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
                if math.isfinite(parsed):
                    return parsed
            except ValueError:
                pass
        if isinstance(value, bool):
            return 1 if value else 0
        return value

    if schema_type == "integer":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
                if parsed.is_integer():
                    return int(parsed)
            except ValueError:
                pass
        if isinstance(value, bool):
            return 1 if value else 0
        # 5.0 这类整值 float 归为 int（jsonschema 的 integer 不接受 float）
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return value

    if schema_type == "boolean":
        if value is None:
            return False
        if isinstance(value, str):
            if value == "true":
                return True
            if value == "false":
                return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
        return value

    if schema_type == "string":
        if value is None:
            return ""
        if isinstance(value, bool):
            # JSON 语义小写，而非 Python str(True) 的 "True"
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return value

    if schema_type == "null":
        if (
            value == ""
            or value is False
            or (value == 0 and not isinstance(value, bool))
        ):
            return None
        return value

    return value


def _is_changed(original: Any, candidate: Any) -> bool:
    """矫正是否生效。

    用"类型变化或值变化"判断，而不是单纯 ``!=``：
    Python 里 ``False == 0`` 为 True，但 bool 与 int 的互矫正应当算生效（对齐 TS 的 ``!==``）。
    """
    return type(candidate) is not type(original) or candidate != original


def _coerce_with_union_schema(value: Any, schemas: List[Any]) -> Any:
    """anyOf/oneOf：对每个子 schema 试矫正并用其校验，第一个通过的胜出。"""
    for sub in schemas:
        if not isinstance(sub, dict):
            continue
        candidate = _coerce_with_json_schema(copy.deepcopy(value), sub)
        try:
            if jsonschema.Draft7Validator(sub).is_valid(candidate):
                return candidate
        except Exception:
            # 子 schema 本身无效时视为不匹配，继续尝试下一个
            continue
    return value


def _coerce_object_properties(value: Dict[str, Any], schema: Dict[str, Any]) -> None:
    """原地矫正 object 的 properties 与 additionalProperties。"""
    properties = schema.get("properties")
    defined_keys = set(properties.keys()) if isinstance(properties, dict) else set()

    if isinstance(properties, dict):
        for key, property_schema in properties.items():
            if key in value and isinstance(property_schema, dict):
                value[key] = _coerce_with_json_schema(value[key], property_schema)

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        for key in list(value.keys()):
            if key not in defined_keys:
                value[key] = _coerce_with_json_schema(value[key], additional)


def _coerce_array_items(value: List[Any], schema: Dict[str, Any]) -> None:
    """原地矫正 array 的 items（list 按位置，dict 统一）。"""
    items = schema.get("items")
    if isinstance(items, list):
        for index in range(len(value)):
            if index < len(items) and isinstance(items[index], dict):
                value[index] = _coerce_with_json_schema(value[index], items[index])
    elif isinstance(items, dict):
        for index in range(len(value)):
            value[index] = _coerce_with_json_schema(value[index], items)


def _coerce_with_json_schema(value: Any, schema: Any) -> Any:
    """按 JSON Schema 对值做 best-effort 类型矫正（对齐 TS coerceWithJsonSchema）。"""
    if not isinstance(schema, dict):
        return value

    next_value = value

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for nested in all_of:
            next_value = _coerce_with_json_schema(next_value, nested)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        next_value = _coerce_with_union_schema(next_value, any_of)

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        next_value = _coerce_with_union_schema(next_value, one_of)

    schema_types = _get_schema_types(schema)
    matches_union_member = len(schema_types) > 1 and any(
        _matches_json_type(next_value, t) for t in schema_types
    )
    if schema_types and not matches_union_member:
        for schema_type in schema_types:
            candidate = _coerce_primitive(next_value, schema_type)
            if _is_changed(next_value, candidate):
                next_value = candidate
                break

    if "object" in schema_types and isinstance(next_value, dict):
        _coerce_object_properties(next_value, schema)

    if "array" in schema_types and isinstance(next_value, list):
        _coerce_array_items(next_value, schema)

    return next_value


# ----------------------------------------------------------------------
# 校验入口
# ----------------------------------------------------------------------


def validate_tool_call(tools: List[Tool], tool_call: ToolCall) -> Any:
    """
    通过名称查找工具，并验证工具调用参数

    Args:
        tools: 工具定义列表
        tool_call: 来自LLM的工具调用

    Returns:
        验证并矫正后的参数字典

    Raises:
        ValueError: 如果工具未找到或验证失败
    """
    # 查找工具
    tool = None
    for t in tools:
        if t.name == tool_call.name:
            tool = t
            break

    if tool is None:
        raise ValueError(f'Tool "{tool_call.name}" not found')

    return validate_tool_arguments(tool, tool_call)


def validate_tool_arguments(tool: Tool, tool_call: ToolCall) -> Any:
    """
    根据工具的 JSON Schema 矫正并验证工具调用参数。

    先在 deepcopy 上按 schema 做类型矫正（``"5"`` → ``5`` 等，对齐 TS），
    再执行 jsonschema 校验；schema 本身无效时宽容地原样返回参数。

    Args:
        tool: 工具定义（包含 parameters JSON Schema）
        tool_call: 工具调用对象

    Returns:
        验证并矫正后的参数字典

    Raises:
        ValueError: 如果验证失败，包含格式化的错误信息
    """
    schema = tool.parameters
    if schema is None:
        # 没有schema，无需验证
        return tool_call.arguments

    # 克隆参数以避免修改原始数据，然后按 schema 矫正类型
    args = _coerce_with_json_schema(copy.deepcopy(tool_call.arguments), schema)

    try:
        errors = list(jsonschema.Draft7Validator(schema).iter_errors(args))
    except Exception:
        # schema 本身无效（如版本不兼容）时宽容跳过校验
        return args

    if not errors:
        return args

    # 格式化错误信息
    error_lines = []
    for err in errors:
        # 获取错误路径
        path = ".".join(str(p) for p in err.path) if err.path else "root"
        # 如果是缺少属性，从validator的上下文获取
        if err.validator == "required":
            missing = err.message.split("'")[1] if "'" in err.message else "unknown"
            path = missing
            message = "is required"
        else:
            message = err.message
        error_lines.append(f"  - {path}: {message}")

    error_msg = "\n".join(error_lines)
    args_str = json.dumps(tool_call.arguments, indent=2, ensure_ascii=False)

    raise ValueError(
        f'Validation failed for tool "{tool_call.name}":\n{error_msg}\n\nReceived arguments:\n{args_str}'
    )


__all__ = [
    "default_convert_to_llm",
    "validate_tool_call",
    "validate_tool_arguments",
]
