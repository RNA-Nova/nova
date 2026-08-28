"""Agents 域（agent 组合声明的会话期消费）。

- ``AgentManager``：agents 注册表活视图 + 当前角色旋钮（change_agent /
  默认解析链）+ 可委派视图与菜单注入数据 + CapabilitySelection 汇集 +
  yaml 写回（/agent save 落地，包来源影子写 user 级）。
"""

from nova_harness.core.harness.agents.manager import BASE_AGENT_NAME, AgentManager

__all__ = ["AgentManager", "BASE_AGENT_NAME"]
