"""
系统提示词构建。

- `SystemPromptManager` 负责 agent 切换、工具白名单、扩展工具合并。
- `SystemPromptBuilder`（`builder.py`）负责把 Agent 配置渲染成最终系统提示词字符串。

相关数据类型见 `types/agent_config.py`。
"""

from nova_harness.core.harness.system_prompt.manager import SystemPromptManager

__all__ = ["SystemPromptManager"]
