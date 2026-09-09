"""
工具参数校验与类型矫正测试

对齐 TS pi-ai utils/validation.ts 的行为：
validate_tool_arguments 在 jsonschema 校验前按 schema 做类型矫正（coercion）。
"""

import pytest
from nova_agent.utils import validate_tool_arguments
from nova_ai import Tool, ToolCall


def _tool(schema: dict) -> Tool:
    return Tool(name="t", description="test tool", parameters=schema)


def _call(args: dict) -> ToolCall:
    return ToolCall(id="tc-1", name="t", arguments=args)


# ----------------------------------------------------------------------
# 原始类型矫正
# ----------------------------------------------------------------------


def test_coerce_string_to_integer():
    tool = _tool({"type": "object", "properties": {"x": {"type": "integer"}}})
    assert validate_tool_arguments(tool, _call({"x": "5"})) == {"x": 5}


def test_coerce_integer_valued_float_string_to_integer():
    """ "5.0" 经 is_integer 判定可矫正为 int（对齐 TS Number.isInteger）。"""
    tool = _tool({"type": "object", "properties": {"x": {"type": "integer"}}})
    assert validate_tool_arguments(tool, _call({"x": "5.0"})) == {"x": 5}


def test_non_integer_string_stays_and_fails_validation():
    """ "5.5" 不能塞进 integer：保持原值并在校验阶段报错。"""
    tool = _tool({"type": "object", "properties": {"x": {"type": "integer"}}})
    with pytest.raises(ValueError, match="Validation failed"):
        validate_tool_arguments(tool, _call({"x": "5.5"}))


def test_coerce_string_to_number():
    tool = _tool({"type": "object", "properties": {"x": {"type": "number"}}})
    assert validate_tool_arguments(tool, _call({"x": "5.5"})) == {"x": 5.5}


def test_coerce_bool_to_number():
    tool = _tool({"type": "object", "properties": {"x": {"type": "number"}}})
    assert validate_tool_arguments(tool, _call({"x": True})) == {"x": 1}


def test_coerce_none_to_number():
    tool = _tool({"type": "object", "properties": {"x": {"type": "number"}}})
    assert validate_tool_arguments(tool, _call({"x": None})) == {"x": 0}


def test_coerce_string_to_boolean():
    tool = _tool({"type": "object", "properties": {"flag": {"type": "boolean"}}})
    assert validate_tool_arguments(tool, _call({"flag": "true"})) == {"flag": True}
    assert validate_tool_arguments(tool, _call({"flag": "false"})) == {"flag": False}


def test_coerce_number_to_boolean():
    tool = _tool({"type": "object", "properties": {"flag": {"type": "boolean"}}})
    assert validate_tool_arguments(tool, _call({"flag": 1})) == {"flag": True}
    assert validate_tool_arguments(tool, _call({"flag": 0})) == {"flag": False}


def test_coerce_to_string_uses_json_semantics():
    """bool → string 用 JSON 语义小写（不是 Python 的 "True"）。"""
    tool = _tool({"type": "object", "properties": {"s": {"type": "string"}}})
    assert validate_tool_arguments(tool, _call({"s": True})) == {"s": "true"}
    assert validate_tool_arguments(tool, _call({"s": 5})) == {"s": "5"}


def test_coerce_to_null():
    tool = _tool({"type": "object", "properties": {"v": {"type": "null"}}})
    assert validate_tool_arguments(tool, _call({"v": ""})) == {"v": None}
    assert validate_tool_arguments(tool, _call({"v": 0})) == {"v": None}
    assert validate_tool_arguments(tool, _call({"v": False})) == {"v": None}


def test_bool_not_treated_as_number():
    """Python 的 bool 是 int 子类：True 不能被当作 integer 蒙混过关。"""
    tool = _tool({"type": "object", "properties": {"x": {"type": "integer"}}})
    # True 矫正为 1（与 TS 一致），而不是作为 bool 直接通过 integer 校验
    assert validate_tool_arguments(tool, _call({"x": True})) == {"x": 1}


def test_uncoercible_value_fails_with_formatted_error():
    tool = _tool(
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
    )
    with pytest.raises(ValueError, match="Validation failed"):
        validate_tool_arguments(tool, _call({"x": "abc"}))


def test_missing_required_fails():
    tool = _tool(
        {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
    )
    with pytest.raises(ValueError, match="is required"):
        validate_tool_arguments(tool, _call({}))


# ----------------------------------------------------------------------
# 嵌套矫正
# ----------------------------------------------------------------------


def test_coerce_nested_object_properties():
    tool = _tool(
        {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"x": {"type": "integer"}},
                }
            },
        }
    )
    assert validate_tool_arguments(tool, _call({"nested": {"x": "5"}})) == {
        "nested": {"x": 5}
    }


def test_coerce_array_items():
    tool = _tool(
        {
            "type": "object",
            "properties": {"xs": {"type": "array", "items": {"type": "integer"}}},
        }
    )
    assert validate_tool_arguments(tool, _call({"xs": ["1", "2", "3"]})) == {
        "xs": [1, 2, 3]
    }


def test_coerce_additional_properties():
    tool = _tool(
        {
            "type": "object",
            "properties": {"known": {"type": "string"}},
            "additionalProperties": {"type": "integer"},
        }
    )
    assert validate_tool_arguments(tool, _call({"known": "a", "extra": "5"})) == {
        "known": "a",
        "extra": 5,
    }


# ----------------------------------------------------------------------
# union / anyOf / allOf
# ----------------------------------------------------------------------


def test_union_type_keeps_matching_value_unchanged():
    """值已匹配 union 成员类型时不矫正。"""
    tool = _tool(
        {
            "type": "object",
            "properties": {"v": {"type": ["string", "integer"]}},
        }
    )
    assert validate_tool_arguments(tool, _call({"v": "hello"})) == {"v": "hello"}
    assert validate_tool_arguments(tool, _call({"v": 5})) == {"v": 5}


def test_union_type_coerces_when_no_member_matches():
    tool = _tool(
        {
            "type": "object",
            "properties": {"v": {"type": ["string", "integer"]}},
        }
    )
    # True 不匹配 string 也不匹配 integer：按顺序先矫正为 string
    assert validate_tool_arguments(tool, _call({"v": True})) == {"v": "true"}


def test_any_of_coercion():
    tool = _tool(
        {
            "type": "object",
            "properties": {"v": {"anyOf": [{"type": "integer"}, {"type": "string"}]}},
        }
    )
    assert validate_tool_arguments(tool, _call({"v": "5"})) == {"v": 5}


def test_all_of_coercion():
    tool = _tool(
        {
            "type": "object",
            "properties": {
                "v": {
                    "allOf": [
                        {"type": "integer"},
                        {"type": "number", "minimum": 0},
                    ]
                }
            },
        }
    )
    assert validate_tool_arguments(tool, _call({"v": "5"})) == {"v": 5}


# ----------------------------------------------------------------------
# 其他行为
# ----------------------------------------------------------------------


def test_original_arguments_not_mutated():
    """deepcopy 保证 tool_call.arguments 不被矫正过程污染。"""
    tool = _tool({"type": "object", "properties": {"x": {"type": "integer"}}})
    call = _call({"x": "5"})
    validate_tool_arguments(tool, call)
    assert call.arguments == {"x": "5"}


def test_no_schema_returns_arguments_as_is():
    tool = Tool(name="t", description="d", parameters=None)
    assert validate_tool_arguments(tool, _call({"x": "5"})) == {"x": "5"}


def test_invalid_schema_leniently_returns_args():
    """schema 本身无法被 jsonschema 处理时宽容跳过校验。"""
    tool = _tool({"type": "object", "properties": {"x": {"type": 123}}})
    assert validate_tool_arguments(tool, _call({"x": "5"})) == {"x": "5"}
