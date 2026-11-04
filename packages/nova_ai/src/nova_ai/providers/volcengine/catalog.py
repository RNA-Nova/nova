"""Volcengine 账户私活（``personalize`` 钩子：ark 在售筛选）。

钩子经 ``create_provider(personalize=...)`` 装配进 personalized 刷新策略
（``gateway/strategies.py``）——恢复/拉站/分流/持久化/发布全部归策略；
本模块只含账户层两件事：拉取（ark ``/models`` 在售状态表）、筛选
（显式 Shutdown/Retiring 剔除；无凭据匿名不筛）。
"""

from typing import Dict, List, Optional

from nova_protocol import AbortedError, AbortSignal, Model, NovaDiagnostic, WarningKind

from ...gateway.provider import RefreshModelsContext
from ...utils.http import fetch_json

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/"
"""本家 API endpoint（provider 工厂与目录盖戳共用同一份）。"""

ARK_MODELS_URL = "https://ark.cn-beijing.volces.com/api/v3/models"
ARK_BAD_STATUS = {"Shutdown", "Retiring"}


async def fetch_ark_status(
    api_key: Optional[str], signal: Optional[AbortSignal] = None
) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表：``{模型 id: status}``（仅显式坏状态）。

    ``None`` = 无状态表（无 key / 载荷非 dict）。传输失败不再静默吞——
    抛给调用方裁决（best-effort 筛选的失效要能被感知）。
    """
    if not api_key:
        return None
    payload = await fetch_json(ARK_MODELS_URL, api_key=api_key, signal=signal)
    # 解析同属 best-effort 区：非 dict 载荷/畸形条目一律按无状态表处理
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


async def ark_personalize(
    base: List[Model],
    context: RefreshModelsContext,
) -> List[Model]:
    """火山 ``personalize`` 钩子：ark 在售筛选（账户生命周期事实）。

    凭据带 key 时剔除显式 Shutdown/Retiring；无凭据匿名不筛（基底原样）。
    筛选失败（传输层）不拖垮目录——发诊断、基底照常。
    """
    credential = context.credential
    api_key: Optional[str] = None
    if credential is not None:
        api_key = credential.access if credential.type == "oauth" else credential.key
    if not api_key:
        return base
    try:
        ark_status = await fetch_ark_status(api_key, context.signal)
    except AbortedError:
        raise  # 取消直通（铁律三）
    except Exception:
        # best-effort 筛选失效不该静默：发 CAPABILITY 诊断，目录照常
        context.diagnostics.append(
            NovaDiagnostic(
                severity="warning",
                kind=WarningKind.CAPABILITY,
                message=(
                    "volcengine ark 在售状态拉取失败——本次刷新未过滤"
                    "下架/退役模型（Shutdown/Retiring 筛选未生效）"
                ),
            )
        )
        return base
    if not ark_status:
        return base
    return [m for m in base if m.id not in ark_status]


__all__ = [
    "BASE_URL",
    "ark_personalize",
    "fetch_ark_status",
]
