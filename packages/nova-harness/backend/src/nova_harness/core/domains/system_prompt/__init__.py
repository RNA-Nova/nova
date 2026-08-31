"""
系统提示词构建。

- `SystemPromptManager` 纯渲染（config + 各 manager → 文本）：当前角色名
  经 `AgentManager` 活取，工具白名单经 `ToolsManager`，persona 装配经
  `PersonaManager`；可委派 agent 菜单（`# Available Agents`）随激活工具
  含 `subagent` 注入。
- `SystemPromptBuilder`（`builder.py`）负责把 Agent 配置渲染成最终系统提示词字符串。

相关数据类型见 `types/resources/agents.py`。
"""

from nova_harness.core.harness.system_prompt.manager import SystemPromptManager

__all__ = ["SystemPromptManager"]
