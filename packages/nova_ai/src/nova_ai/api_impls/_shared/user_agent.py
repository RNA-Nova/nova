"""User-Agent 头（对齐 TS ``src/utils/pi-user-agent.ts``，nova 版本）。"""

import platform

__all__ = ["get_nova_user_agent"]


def get_nova_user_agent() -> str:
    """``nova-ai (<platform> <release>; <arch>)`` 形态的 UA，便于 provider 侧识别。"""
    return f"nova-ai ({platform.system().lower()} {platform.release()}; {platform.machine()})"
