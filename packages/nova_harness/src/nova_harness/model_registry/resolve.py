"""
Resolve configuration values that may be shell commands, environment variables, or literals.
"""

import os
import subprocess
import threading
from typing import Dict, Optional

# Cache for shell command results (persists for process lifetime)
_command_result_cache: Dict[str, Optional[str]] = {}
_cache_lock = threading.Lock()


def resolve_config_value(config: str) -> Optional[str]:
    """Resolve a config value (API key, header value, etc.) to an actual value."""
    if config.startswith("!"):
        return _execute_command(config)
    
    env_value = os.environ.get(config)
    return env_value or config


def _execute_command(command_config: str) -> Optional[str]:
    """Execute a shell command and return its output."""
    # Check cache with lock
    with _cache_lock:
        if command_config in _command_result_cache:
            return _command_result_cache[command_config]
    
    # Execute command (outside lock to avoid blocking)
    command = command_config[1:]
    result: Optional[str] = None
    
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
        result = trimmed if trimmed else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        result = None
    
    # Store result with lock
    with _cache_lock:
        _command_result_cache[command_config] = result
    
    return result


def resolve_headers(headers: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Resolve all header values using the same resolution logic as API keys."""
    if not headers:
        return None
    
    resolved: Dict[str, str] = {}
    for key, value in headers.items():
        resolved_value = resolve_config_value(value)
        if resolved_value is not None:
            resolved[key] = resolved_value
    
    return resolved if resolved else None


def clear_config_value_cache() -> None:
    """Clear the config value command cache. Exported for testing."""
    with _cache_lock:
        _command_result_cache.clear()