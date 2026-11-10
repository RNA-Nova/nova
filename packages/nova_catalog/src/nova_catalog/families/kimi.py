"""Kimi K3 模型族的跨宿主共享事实（pi ``catalog/families/kimi.ts``）。"""

import re
from typing import Dict

# K3 族 openai-completions 协议侧 compat；协议戳仍归各家出厂声明。
K3_OPENAI_COMPAT: Dict[str, object] = {
    "thinking_format": "openai",
    "supports_reasoning_effort": True,
    "requires_reasoning_content_on_assistant_messages": True,
}


def is_kimi_k3_family_id(model_id: str) -> bool:
    """K3 及点发布/后缀；不把 kimi-k30 或多段点号误归本族。"""
    return re.match(r"^kimi-k3(?:\.\d+)?(?:-|$)", model_id) is not None


__all__ = ["K3_OPENAI_COMPAT", "is_kimi_k3_family_id"]
