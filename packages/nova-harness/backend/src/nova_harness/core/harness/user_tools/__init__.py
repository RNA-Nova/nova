"""用户工具（user tool）子系统。

用户/前端触发、执行结果以自定义消息类型记录并主动注入 LLM 上下文的
宿主能力。框架不内置任何用户工具——具体工具（如 bash）由包经
``[tool.nova] user_tools`` 类目分发（见 ``examples/user_tools_design.md``）。
"""

from nova_harness.core.harness.user_tools.manager import UserToolManager

__all__ = [
    "UserToolManager",
]
