"""Volcengine 账户私活（``personalize`` 钩子：ark 在售筛选）。

钩子经 ``create_provider(personalize=...)`` 装配进 personalized 刷新策略
（``gateway/strategies.py``）——恢复/拉站/分流/持久化/发布全部归策略；
本模块只含账户层两件事：拉取（ark ``/models`` 在售状态表——运行期
带 signal 的传输路径；URL/坏状态集/解析单点在
``nova_catalog.sources.ark``）、筛选（显式 Shutdown/Retiring 剔除，
钉住模型豁免；无凭据匿名不筛）。
"""

from typing import Dict, Optional, Sequence, Set

from nova_async import AbortedError, AbortSignal
from nova_protocol import AnyModel

from nova_catalog.sources.ark import parse_ark_status

from ...gateway.catalog import CatalogError
from ...gateway.provider import RefreshModelsContext
from ...utils.http import fetch_json

BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/"
"""本家 API endpoint（provider 工厂与目录盖戳共用同一份）。"""


async def fetch_ark_status(
    api_key: Optional[str],
    signal: Optional[AbortSignal] = None,
    base_url: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """火山方舟在售状态表（运行期拉取路径——带 signal；解析单点在
    ``nova_catalog.sources.ark.parse_ark_status``）。

    ``None`` = 无状态表（无 key / 载荷非 dict）。传输失败不再静默吞——
    抛给调用方裁决（有凭据时失败不得冒充“无需筛选”）。
    """
    if not api_key:
        return None
    endpoint = (base_url or BASE_URL).rstrip("/")
    payload = await fetch_json(f"{endpoint}/models", api_key=api_key, signal=signal)
    return parse_ark_status(payload)


async def ark_personalize(
    base: Sequence[AnyModel],
    context: RefreshModelsContext,
    exempt_ids: Optional[Set[str]] = None,
) -> Sequence[AnyModel]:
    """火山 ``personalize`` 钩子：ark 在售筛选（账户生命周期事实）。

    凭据带 key 时剔除显式 Shutdown/Retiring；钉住模型经 ``exempt_ids`` 豁免。
    无凭据匿名不筛。有凭据时拉取/解析失败抛错，策略层保留旧 personalized
    成品，不把未筛选基底写盘。
    """
    credential = context.credential
    api_key: Optional[str] = None
    if credential is not None:
        api_key = credential.access if credential.type == "oauth" else credential.key
    if not api_key:
        return base
    try:
        ark_status = await fetch_ark_status(
            api_key, context.signal, base_url=context.base_url
        )
    except AbortedError:
        raise  # 取消直通（铁律三）
    except Exception as error:
        # 有凭据时筛选失败不得把未过滤基底作为 personalized 成品落盘。
        raise CatalogError("Volcengine Ark lifecycle catalog failed") from error
    if ark_status is None:
        raise CatalogError("Volcengine Ark lifecycle payload is invalid")
    if not ark_status:
        return base
    exempt = exempt_ids or set()
    return [m for m in base if m.id not in ark_status or m.id in exempt]


__all__ = [
    "BASE_URL",
    "ark_personalize",
    "fetch_ark_status",
]
