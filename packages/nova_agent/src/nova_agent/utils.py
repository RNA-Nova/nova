"""
工具调用验证器
使用JSON Schema验证工具调用的参数
"""

import copy
import json
from typing import List, Any, Dict
import jsonschema

from nova_ai import Tool, ToolCall

# 全局配置：是否启用验证（可以设置为False以禁用，模拟浏览器环境）
ENABLE_VALIDATION = True

# 缓存编译后的schema验证器，提高性能
_validator_cache: Dict[str, Any] = {}


def validate_tool_call(tools: List[Tool], tool_call: ToolCall) -> Any:
    """
    通过名称查找工具，并验证工具调用参数

    Args:
        tools: 工具定义列表
        tool_call: 来自LLM的工具调用

    Returns:
        验证后的参数字典（可能经过类型转换）

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
    根据工具的JSON Schema验证工具调用参数

    Args:
        tool: 工具定义（包含parameters JSON Schema）
        tool_call: 工具调用对象

    Returns:
        验证后的参数字典（可能经过类型转换）

    Raises:
        ValueError: 如果验证失败，包含格式化的错误信息
    """
    # 如果全局禁用验证，直接返回原始参数
    if not ENABLE_VALIDATION:
        return tool_call.arguments

    # 获取schema
    schema = tool.parameters
    if schema is None:
        # 没有schema，无需验证
        return tool_call.arguments

    # 克隆参数以避免修改原始数据
    args = copy.deepcopy(tool_call.arguments)

    # 编译或获取缓存的验证器
    # 使用工具名称和schema的字符串表示作为缓存键
    cache_key = f"{tool.name}_{json.dumps(schema, sort_keys=True)}"
    if cache_key not in _validator_cache:
        # 创建验证器（jsonschema.Draft7Validator 或根据schema版本选择）
        try:
            # 尝试使用默认的Draft7Validator
            validator = jsonschema.Draft7Validator(schema)
            _validator_cache[cache_key] = validator
        except Exception:
            # 如果schema版本不兼容，回退到基础验证
            _validator_cache[cache_key] = None
            # 如果没有验证器，直接返回参数
            return args
    else:
        validator = _validator_cache.get(cache_key)

    if validator is None:
        return args

    # 执行验证
    errors = list(validator.iter_errors(args))
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


def set_validation_enabled(enabled: bool) -> None:
    """
    设置是否启用验证（用于测试或特殊环境）
    """
    global ENABLE_VALIDATION
    ENABLE_VALIDATION = enabled


def clear_validator_cache() -> None:
    """清空验证器缓存"""
    _validator_cache.clear()
