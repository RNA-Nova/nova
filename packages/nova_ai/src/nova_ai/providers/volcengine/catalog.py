"""Volcengine 账户私活（``personalize`` 钩子：ark 在售筛选）。

钩子经 ``create_provider(personalize=...)`` 装配进 personalized 刷新策略
（``gateway/strategies.py``）——恢复/拉站/分流/持久化/发布全部归策略；
本模块只含账户层两件事：拉取（ark ``/models`` 在售状态表——运行期
带 signal 的传输路径；URL/坏状态集/解析单点在
``nova_catalog.sources.ark``）、筛选（显式 Shutdown/Retiring 剔除，
钉住模型豁免；无凭据匿名不筛）。
"""

from typing import Dict, List, Optional, Set

from nova_catalog.sources.ark import ARK_MODELS_URL, parse_ark_status
from nova_protocol import AbortedError, AbortSignal, Model, NovaDiagnostic, WarningKind

from ...gateway.provider import RefreshModelsContext
from ...utils.http import fetch_json

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/"
"""本家 API endpoint（provider 工厂与目录盖戳共用同一份）。"""


async def fetch_ark_status(
    api_key: Optional[str], signal: Optional[AbortSignal] = None
) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表（运行期拉取路径——带 signal；解析单点在
    ``nova_catalog.sources.ark.parse_ark_status``）。

    ``None`` = 无状态表（无 key / 载荷非 dict）。传输失败不再静默吞——
    抛给调用方裁决（best-effort 筛选的失效要能被感知）。
    """
    if not api_key:
        return None
    payload = await fetch_json(ARK_MODELS_URL, api_key=api_key, signal=signal)
    return parse_ark_status(payload)


async def ark_personalize(
    base: List[Model],
    context: RefreshModelsContext,
    exempt_ids: Optional[Set[str]] = None,
) -> List[Model]:
    """火山 ``personalize`` 钩子：ark 在售筛选（账户生命周期事实）。

    凭据带 key 时剔除显式 Shutdown/Retiring——**钉住模型豁免**（数据源
    缺失但端点实测在售，ark 状态表误伤它们；与构建期
    ``_ark_lifecycle_filter`` 同一套规则，``exempt_ids`` 由 DEF 注入）。
    无凭据匿名不筛（基底原样）。筛选失败（传输层）不拖垮目录——发诊断、
    基底照常。
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
    exempt = exempt_ids or set()
    return [m for m in base if m.id not in ark_status or m.id in exempt]


__all__ = [
    "BASE_URL",
    "ark_personalize",
    "fetch_ark_status",
]
