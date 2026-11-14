"""火山方舟（ark）账户级生命周期响应的共享解析。

模型在售/退役状态按账户+区域变化，只由 nova_ai 的运行时 personalize 拉取；
本模块不进入公共种子构建，也不持有第二份无取消的传输实现。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

ARK_BAD_STATUS = {"Shutdown", "Retiring"}


def parse_ark_status(payload: Any) -> Optional[Dict[str, str]]:
    """ark ``/models`` 载荷 → ``{模型 id: status}``（仅显式坏状态）。"""
    if not isinstance(payload, dict):
        return None
    entries = payload.get("data")
    if not isinstance(entries, list):
        return None
    status_map: Dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in ARK_BAD_STATUS:
            model_id = entry.get("id")
            # 坏状态却没有合法 id 时无法安全筛选；整份载荷按无效处理。
            if not isinstance(model_id, str) or not model_id:
                return None
            status_map[model_id] = status
    return status_map


__all__ = ["ARK_BAD_STATUS", "parse_ark_status"]
