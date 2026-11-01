"""Volcengine 目录内容源（``fetch_models`` 回调 + 方舟在售状态机器）。

models.dev 公开站基底（``context.catalog_fetch``——manager 按
``catalog_source="models.dev"`` 发放）+ 凭据带 key 时叠方舟在售筛选
（显式 Shutdown/Retiring 剔除；无凭据匿名不筛）。ark 机器归本目录
（鉴权私活，不进公开站契约）。
"""

from typing import Dict, List, Optional

from nova_catalog.providers.volcengine import DEF as _knowledge
from nova_protocol import (
    AbortedError,
    AbortSignal,
    Model,
    NovaDiagnostic,
    WarningKind,
    is_aborted,
)

from ...gateway.catalog import CatalogError
from ...gateway.provider import RefreshModelsContext
from ...model_catalog import finalize_catalog_models
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


async def volcengine_fetch_models(
    context: RefreshModelsContext,
) -> Optional[List[Model]]:
    """本家 ``fetch_models`` 回调（三件套治理归 ``_default_refresh_models``）。

    返回 ``None`` = 本次无发布（无站降级——缓存保持现状，基线兜底）。
    """
    if is_aborted(context.signal):
        return None

    fetch = context.catalog_fetch
    if fetch is None:
        # 无站（nova_ai 单用未配容器）——本次无发布，缓存保持现状
        context.diagnostics.append(
            NovaDiagnostic(
                severity="warning",
                kind=WarningKind.CAPABILITY,
                message="volcengine 未配置公开目录站——回退内置基线目录",
            )
        )
        return None
    outcome = await fetch(context.signal, None)
    if outcome.status != 200:
        # 不硬编 UPSTREAM_ERROR：真实非 200 由归一按 status_code 分类，
        # status=0 的合成码场景沿 cause 链取传输层真 kind
        raise CatalogError(
            f"Model catalog request failed for volcengine: {outcome.status}",
            status_code=outcome.status or None,
        ) from outcome.cause
    models = finalize_catalog_models(
        "volcengine",
        BASE_URL,
        outcome.models_fields,
        knowledge=_knowledge,
    )

    credential = context.credential
    api_key: Optional[str] = None
    if credential is not None:
        api_key = credential.access if credential.type == "oauth" else credential.key
    if api_key:
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
            ark_status = None
        if ark_status:
            models = [m for m in models if m.id not in ark_status]
    return models


__all__ = [
    "fetch_ark_status",
    "volcengine_fetch_models",
]
