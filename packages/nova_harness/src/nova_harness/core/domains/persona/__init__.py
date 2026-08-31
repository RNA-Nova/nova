"""Persona 域（人格资源的会话期消费）。

- ``PersonaManager``：persona 注册表视图 + 装配（路径/注册名 → Section 序列）
  + override 旋钮（内存态会话级人格切换）。
"""

from nova_harness.core.harness.persona.manager import PersonaManager

__all__ = ["PersonaManager"]
