"""火山方舟（ark）在售状态源（对位 models.dev 的 provider 私有上游）。

方舟是云控制台语义：模型在售/退役/下线状态按账户+区域走，任何公开
目录站都不携带——它是 volcengine 的"账户级生命周期源"，与 models.dev
（公共目录源）平级。构建期与运行期共用本模块（运行期经
``nova_ai.providers.volcengine.catalog`` 调用——同一份源定义）。
"""

from __future__ import annotations

from typing import Dict, Optional

from .http import fetch_json

ARK_MODELS_URL = "https://ark.cn-beijing.volces.com/api/v3/models"
ARK_BAD_STATUS = {"Shutdown", "Retiring"}


async def fetch_ark_status(api_key: Optional[str]) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表：``{模型 id: status}``（仅显式坏状态）。

    ``None`` = 无状态表（无 key / 载荷非 dict）。传输失败抛给调用方
    裁决（best-effort 筛选的失效要能被感知）。
    """
    if not api_key:
        return None
    payload = await fetch_json(ARK_MODELS_URL, api_key=api_key)
    if not isinstance(payload, dict):
        return None
    status_map: Dict[str, str] = {}
    for entry in payload.get("data", []):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in ARK_BAD_STATUS:
            status_map[entry.get("id", "")] = status
    return status_map


__all__ = ["ARK_BAD_STATUS", "ARK_MODELS_URL", "fetch_ark_status"]
