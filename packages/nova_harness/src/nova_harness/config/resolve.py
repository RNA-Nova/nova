"""解析配置值：shell 命令、环境变量引用或字面量。

对齐 TS ``core/resolve-config-value.ts``：

- 以 ``!`` 开头：执行其余部分作为 shell 命令，取 stdout（带缓存）
- ``$ENV_VAR`` / ``${ENV_VAR}``：环境变量插值；任一缺失则整体解析失败
- 非命令值中 ``$$`` 转义为字面 ``$``，``$!`` 转义为字面 ``!``
- 其他情况按字面量处理
"""

import os
import re
import subprocess
import threading
from typing import Dict, List, Optional, Tuple

# shell 命令结果缓存（进程生命周期内有效）
_command_result_cache: Dict[str, Optional[str]] = {}
_cache_lock = threading.Lock()

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENV_VAR_NAME_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*")

# 模板片段：("literal", text) 或 ("env", name)
_TemplatePart = Tuple[str, str]
# 配置值引用：("command", config) 或 ("template", parts)
_ConfigValueReference = Tuple[str, object]


def clear_config_value_cache() -> None:
    """清空命令结果缓存（配置重载后应重新解析）。"""
    with _cache_lock:
        _command_result_cache.clear()


def _append_literal(parts: List[_TemplatePart], value: str) -> None:
    if not value:
        return
    if parts and parts[-1][0] == "literal":
        parts[-1] = ("literal", parts[-1][1] + value)
        return
    parts.append(("literal", value))


def _parse_template(config: str) -> List[_TemplatePart]:
    parts: List[_TemplatePart] = []
    index = 0
    length = len(config)

    while index < length:
        dollar_index = config.find("$", index)
        if dollar_index < 0:
            _append_literal(parts, config[index:])
            break

        _append_literal(parts, config[index:dollar_index])
        next_char = config[dollar_index + 1] if dollar_index + 1 < length else ""

        if next_char in ("$", "!"):
            _append_literal(parts, next_char)
            index = dollar_index + 2
            continue

        if next_char == "{":
            end_index = config.find("}", dollar_index + 2)
            if end_index < 0:
                _append_literal(parts, "$")
                index = dollar_index + 1
                continue
            name = config[dollar_index + 2 : end_index]
            if _ENV_VAR_NAME_RE.match(name):
                parts.append(("env", name))
            else:
                _append_literal(parts, config[dollar_index : end_index + 1])
            index = end_index + 1
            continue

        match = _ENV_VAR_NAME_PREFIX_RE.match(config[dollar_index + 1 :])
        if match:
            parts.append(("env", match.group(0)))
            index = dollar_index + 1 + len(match.group(0))
            continue

        _append_literal(parts, "$")
        index = dollar_index + 1

    return parts


def _parse_reference(config: str) -> _ConfigValueReference:
    if config.startswith("!"):
        return ("command", config)
    return ("template", _parse_template(config))


def _resolve_env(name: str, env: Optional[Dict[str, str]] = None) -> Optional[str]:
    if env is not None and env.get(name):
        return env[name]
    return os.environ.get(name) or None


def _resolve_template(
    parts: List[_TemplatePart], env: Optional[Dict[str, str]] = None
) -> Optional[str]:
    resolved = ""
    for part_type, value in parts:
        if part_type == "literal":
            resolved += value
            continue
        env_value = _resolve_env(value, env)
        if env_value is None:
            return None
        resolved += env_value
    return resolved


def get_config_value_env_var_name(config: str) -> Optional[str]:
    """配置值恰好是单个 env 引用时，返回该变量名。"""
    ref_type, ref = _parse_reference(config)
    if ref_type != "template":
        return None
    parts: List[_TemplatePart] = ref  # type: ignore[assignment]
    if len(parts) == 1 and parts[0][0] == "env":
        return parts[0][1]
    return None


def get_config_value_env_var_names(config: str) -> List[str]:
    """返回模板中引用的全部环境变量名（去重，保持顺序）。"""
    ref_type, ref = _parse_reference(config)
    if ref_type != "template":
        return []
    names: List[str] = []
    for part_type, value in ref:  # type: ignore[union-attr]
        if part_type == "env" and value not in names:
            names.append(value)
    return names


def get_missing_config_value_env_var_names(
    config: str, env: Optional[Dict[str, str]] = None
) -> List[str]:
    return [
        name
        for name in get_config_value_env_var_names(config)
        if _resolve_env(name, env) is None
    ]


def is_command_config_value(config: str) -> bool:
    return _parse_reference(config)[0] == "command"


def is_config_value_configured(
    config: str, env: Optional[Dict[str, str]] = None
) -> bool:
    return len(get_missing_config_value_env_var_names(config, env)) == 0


def _execute_command_uncached(command_config: str) -> Optional[str]:
    command = command_config[1:]
    try:
        output = subprocess.check_output(
            command,
            shell=True,
            encoding="utf-8",
            timeout=10,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        trimmed = output.strip()
        return trimmed if trimmed else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def _execute_command(command_config: str) -> Optional[str]:
    with _cache_lock:
        if command_config in _command_result_cache:
            return _command_result_cache[command_config]

    result = _execute_command_uncached(command_config)

    with _cache_lock:
        _command_result_cache[command_config] = result
    return result


def resolve_config_value(
    config: str, env: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """解析配置值（API key、header 值等）为实际值。"""
    ref_type, ref = _parse_reference(config)
    if ref_type == "command":
        return _execute_command(ref)  # type: ignore[arg-type]
    return _resolve_template(ref, env)  # type: ignore[arg-type]


def resolve_config_value_uncached(
    config: str, env: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """同 resolve_config_value，但命令型配置不走缓存。"""
    ref_type, ref = _parse_reference(config)
    if ref_type == "command":
        return _execute_command_uncached(ref)  # type: ignore[arg-type]
    return _resolve_template(ref, env)  # type: ignore[arg-type]


def resolve_config_value_or_throw(
    config: str, description: str, env: Optional[Dict[str, str]] = None
) -> str:
    """解析配置值，失败时抛出带具体缺失变量名的错误。"""
    resolved = resolve_config_value_uncached(config, env)
    if resolved is not None:
        return resolved

    ref_type, ref = _parse_reference(config)
    if ref_type == "command":
        raise ValueError(
            f"Failed to resolve {description} from shell command: " f"{str(ref)[1:]}"
        )

    missing = get_missing_config_value_env_var_names(config, env)
    if len(missing) == 1:
        raise ValueError(
            f"Failed to resolve {description} from environment variable: "
            f"{missing[0]}"
        )
    if len(missing) > 1:
        raise ValueError(
            f"Failed to resolve {description} from environment variables: "
            f"{', '.join(missing)}"
        )
    raise ValueError(f"Failed to resolve {description}")


def resolve_headers(
    headers: Optional[Dict[str, str]], env: Optional[Dict[str, str]] = None
) -> Optional[Dict[str, str]]:
    """按与 API key 相同的逻辑解析全部 header 值。"""
    if not headers:
        return None
    resolved: Dict[str, str] = {}
    for key, value in headers.items():
        resolved_value = resolve_config_value(value, env)
        if resolved_value:
            resolved[key] = resolved_value
    return resolved if resolved else None


def resolve_headers_or_throw(
    headers: Optional[Dict[str, str]],
    description: str,
    env: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, str]]:
    """同 resolve_headers，但任一值解析失败即抛错。"""
    if not headers:
        return None
    resolved: Dict[str, str] = {}
    for key, value in headers.items():
        resolved[key] = resolve_config_value_or_throw(
            value, f'{description} header "{key}"', env
        )
    return resolved if resolved else None


__all__ = [
    "clear_config_value_cache",
    "get_config_value_env_var_name",
    "get_config_value_env_var_names",
    "get_missing_config_value_env_var_names",
    "is_command_config_value",
    "is_config_value_configured",
    "resolve_config_value",
    "resolve_config_value_uncached",
    "resolve_config_value_or_throw",
    "resolve_headers",
    "resolve_headers_or_throw",
]
