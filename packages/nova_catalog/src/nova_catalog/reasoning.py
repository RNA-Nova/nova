"""思考级别推导（对位 pi ``catalog/reasoning.ts``）。

models.dev v2 的 ``capabilities.reasoning``（官方验证的 effort 值）→
``ThinkingLevelMap``——**站推导级别表**：数据自己能说的不再手写，
手写表（``families/``）退为站未覆盖时的填缺兜底。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from nova_protocol import ThinkingLevelMap

# 已知级别（与 ThinkingLevelMap 的键序一致；off 由 "none" 值推导）
_KNOWN_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


def effort_to_thinking_level_map(
    reasoning: Optional[Dict[str, Any]],
) -> Optional[ThinkingLevelMap]:
    """``capabilities.reasoning`` → 思考级别表（缺席级别 = 不支持）。

    - ``effort`` 列表中的值直通（``low`` → ``"low"``），缺席 → ``None``；
    - ``off`` 由 ``"none"`` 推导（可关思考）；无 effort 数据 → ``None``
      （纯 toggle 模型，无级别表可推）。
    """
    if not isinstance(reasoning, dict) or reasoning.get("supported") is not True:
        return None
    effort = reasoning.get("effort")
    if not isinstance(effort, list):
        return None
    supported = {v for v in effort if isinstance(v, str)}
    if not supported:
        return None
    level_map: ThinkingLevelMap = {"off": "none" if "none" in supported else None}
    for level in _KNOWN_LEVELS:
        level_map[level] = level if level in supported else None  # type: ignore[literal-required]
    return level_map


__all__ = ["effort_to_thinking_level_map"]
