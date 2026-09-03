"""resolve 模块测试辅助函数。"""

import nova_harness.core.config.resolve as resolve_module


def clear_command_cache() -> None:
    """Clear the module-private command result cache used by resolve_config_value."""
    resolve_module._command_result_cache.clear()
