"""包级消息类型的回载注册表。

框架不内置任何包级消息，其消息类定义在各自的包里。会话 JSONL 反序列化时
按 ``role`` 查本注册表复原消息类型：

- 命中（包已安装并加载）→ 用注册类校验复原；
- 未命中（包缺席）→ 由解析层降级为 ``OpaqueUserToolMessage``，
  原始数据全量保留、默认不进 LLM 上下文。

注册面两入口（同一注册表）：

- 工具/用户工具：``MESSAGE_TYPES`` 类属性约定，加载器
  （``resources/loaders/user_tools.py``）装载时注册；
- 扩展：``NovaExtensionAPI.register_message_types``（装载期声明式注册，
  与 on/registerCommand 同族）。

注册发生在包加载时，早于 ``SessionManager`` 读取 JSONL——资源加载在
services 创建阶段完成，JSONL 解析在 AgentSession 构造阶段开始，时序天然安全。
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Type

from nova_agent import CustomAgentMessage

from nova_harness.core.types.messages import OpaqueUserToolMessage

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Type[CustomAgentMessage]] = {}


def _role_of(cls: Type[CustomAgentMessage]) -> Optional[str]:
    """取消息类的 ``role`` 字段默认值作为注册键。"""
    field = cls.model_fields.get("role")
    if field is None:
        return None
    default = field.default
    return default if isinstance(default, str) and default else None


def register_message_types(types: List[Type[CustomAgentMessage]]) -> None:
    """注册一批消息类（按 ``role`` 判别）。

    幂等语义：**同一个类**重复注册静默跳过（reload 重注册是常态，不是冲突）；
    **不同类**同名 role 才 first-wins + 警告（真碰撞）。
    """
    for cls in types:
        role = _role_of(cls)
        if role is None:
            logger.warning("消息类 %r 缺少 role 字段默认值，跳过注册", cls)
            continue
        existing = _REGISTRY.get(role)
        if existing is cls:
            continue  # reload 幂等重注册——非冲突
        if existing is not None:
            logger.warning(
                "消息 role '%s' 已被 %r 注册，%r 被忽略", role, existing, cls
            )
            continue
        _REGISTRY[role] = cls


def get_session_message_type(role: str) -> Optional[Type[CustomAgentMessage]]:
    """按 role 查注册的消息类；未注册返回 None。"""
    return _REGISTRY.get(role)


def clear_session_message_types() -> None:
    """清空注册表（测试隔离用）。框架静态类型随即重新注册。"""
    _REGISTRY.clear()
    _register_framework_types()


def _register_framework_types() -> None:
    """注册框架静态消息类型（非包提供）。

    ``OpaqueUserToolMessage`` 自身必须可查：降级形态的 JSONL 再次解析时
    命中本注册项直接复原，避免被当作未知 role 二次包装。
    """
    register_message_types([OpaqueUserToolMessage])


_register_framework_types()


__all__ = [
    "clear_session_message_types",
    "get_session_message_type",
    "register_message_types",
]
