"""火山方舟（ark）在售状态源（对位 models.dev 的 provider 私有上游）。

方舟是云控制台语义：模型在售/退役/下线状态按账户+区域走，任何公开
目录站都不携带——它是 volcengine 的"账户级生命周期源"，与 models.dev
（公共目录源）平级。**源定义与解析单点在本模块**（``ARK_MODELS_URL`` /
``ARK_BAD_STATUS`` / ``parse_ark_status``），构建期与运行期共用；
拉取传输各走各家（构建期 ``sources/http.fetch_json`` 无取消概念，
运行期 ``nova_ai.utils.http.fetch_json`` 带 signal——传输层的有意
双轨，语义单点）。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .http import fetch_json

ARK_MODELS_URL = "https://ark.cn-beijing.volces.com/api/v3/models"
ARK_BAD_STATUS = {"Shutdown", "Retiring"}


def parse_ark_status(payload: Any) -> Optional[Dict[str, str]]:
    """ark ``/models`` 载荷 → ``{模型 id: status}``（仅显式坏状态）。

    非 dict 载荷返回 ``None``（无状态表）；畸形条目跳过不拖垮整批。
    构建期与运行期同一解析（本函数是唯一实现点）。
    """
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


async def fetch_ark_status(api_key: Optional[str]) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表（构建期拉取路径——无取消概念）。

    ``None`` = 无状态表（无 key / 载荷非 dict）。传输失败抛给调用方
    裁决（best-effort 筛选的失效要能被感知）。
    """
    if not api_key:
        return None
    payload = await fetch_json(ARK_MODELS_URL, api_key=api_key)
    return parse_ark_status(payload)


__all__ = ["ARK_BAD_STATUS", "ARK_MODELS_URL", "fetch_ark_status", "parse_ark_status"]
