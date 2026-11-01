"""Kimi Coding 目录内容源（``fetch_models`` 回调：公开站基底 + managed 鉴权修正）。

统一形态（与火山同构）：**公开站基底先行**（``context.catalog_fetch``——
manager 按 ``catalog_source="pi.dev"`` 发放，带价格与目录），**鉴权修正层
可选叠加**：有 OAuth 凭据时调 managed ``/models``（订阅权威），以其
清单与活字段（思考级别/context/输入类型）修正基底——价格字段保留基底
的（managed 不提供价格，订阅也有成本提醒）。基底挂了且 managed 可用时
退化为 managed 单独服务（价格落 0，可用性优先）。

协议随站：pi.dev 标 kimi-coding 为 anthropic-messages（无立场层——
方言换血已随 anthropic 协议落地退役）。
"""

import os
from typing import Any, Dict, List, Optional

from nova_catalog.providers.kimi_coding import DEF as _knowledge
from nova_protocol import (
    Model,
    ModelFields,
    NovaDiagnostic,
    NovaErrorKind,
    WarningKind,
    is_aborted,
)

from ...gateway.catalog import CatalogError
from ...gateway.provider import RefreshModelsContext
from ...model_catalog import finalize_catalog_models
from ...utils.http import request_with_abort

BASE_URL = "https://api.kimi.com/coding"
"""本家 API endpoint 协议基址（SDK 自拼 /v1/messages；对齐 pi.dev 标注）。"""

_MANAGED_BASE_URL_DEFAULT = f"{BASE_URL}/v1"
"""managed ``/models`` 订阅源的基址（与协议基址分层——该端点在 /v1 下）。"""

# managed /models 的 think_efforts.valid_efforts 到思考级别表的键序
_KNOWN_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


def _managed_base_url() -> str:
    """managed 端点 base URL（``KIMI_CODE_BASE_URL`` 可覆盖，对齐 kimi-code）。"""
    return (os.environ.get("KIMI_CODE_BASE_URL") or _MANAGED_BASE_URL_DEFAULT).rstrip(
        "/"
    )


def _thinking_level_map(valid_efforts: Optional[List[Any]]) -> Dict[str, Optional[str]]:
    """``think_efforts.valid_efforts`` → 思考级别表（缺席级别 = 不支持）。"""
    valid = {e for e in (valid_efforts or []) if isinstance(e, str)}
    level_map: Dict[str, Optional[str]] = {"off": None}
    for level in _KNOWN_LEVELS:
        level_map[level] = level if level in valid else None
    return level_map


def _to_fields(item: Any) -> Optional["ModelFields"]:
    """managed /models 单条目 → ModelFields（畸形条目跳过，不拖垮整批）。

    纯翻译不盖戳——盖戳（协议戳/基址/compat 修正）统一归
    ``finalize_catalog_models``（与公开站基底同一引擎）。
    """
    if not isinstance(item, dict):
        return None
    model_id = item.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    context_length = item.get("context_length")
    if not isinstance(context_length, int) or context_length <= 0:
        return None

    think_efforts = item.get("think_efforts")
    valid_efforts = (
        think_efforts.get("valid_efforts") if isinstance(think_efforts, dict) else None
    )

    input_types = ["text"]
    if item.get("supports_image_in"):
        input_types.append("image")

    return {
        "id": model_id,
        "name": item.get("display_name") or model_id,
        "reasoning": bool(item.get("supports_reasoning")),
        "thinking_level_map": _thinking_level_map(valid_efforts),
        "input_types": input_types,
        # 价格为零值占位：managed 不提供价格——与基底合并时被
        # 基底的 pi.dev 价格替换；managed 单独服务时保持零值
        "cost": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        # managed 端点的协议族事实（源数据驱动需要字段包自带——
        # kimi-code 的订阅端点即 anthropic 形态，pi.dev 同标注）
        "api": "anthropic-messages",
        "context_window": context_length,
        # managed /models 不提供输出上限，以 context 为界
        # （对齐 kimi-code：不携带独立输出 cap）
        "max_tokens": context_length,
    }


async def _managed_fields(context: RefreshModelsContext) -> Optional[List[ModelFields]]:
    """managed /models 订阅源 → 字段列表（鉴权修正层的活数据）。

    凭据形状不限——coding plan 订阅制同时支持 OAuth 与 API key（Bearer
    都是 token，端点同样认）；无凭据才跳过。返回 ``None`` = managed 端点
    未给出 200（不占修正层，基底照常）；401/402/403 = 凭据失效，抛错
    （refresh 记入 errors，不静默）。
    """
    credential = context.credential
    if credential is None:
        return None
    token = credential.access if credential.type == "oauth" else credential.key
    if not token:
        return None
    try:
        response = await request_with_abort(
            "GET",
            f"{_managed_base_url()}/models",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            timeout=4.0,
            signal=context.signal,
        )
    except RuntimeError:
        # 传输失败（超时/连接失败）：不占修正层、基底照常，但发诊断——
        # 订阅可见性修正悄悄缺失不该无感知（CAPABILITY 降级）。
        # （AbortedError 非 RuntimeError——取消照常传播，不误吞）
        context.diagnostics.append(
            NovaDiagnostic(
                severity="warning",
                kind=WarningKind.CAPABILITY,
                message=(
                    "kimi-coding 订阅源拉取失败（传输层）——本次刷新的"
                    "订阅可见性修正缺失，目录以公开站基底为准"
                ),
            )
        )
        return None

    if response.status_code in (401, 402, 403):
        raise CatalogError(
            f"Kimi Code models auth failed ({response.status_code}): re-login required",
            kind=NovaErrorKind.UNAUTHORIZED,
            status_code=response.status_code,
        )
    if response.status_code != 200:
        return None
    try:
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
    except Exception:
        data = None
    if not isinstance(data, list):
        return None
    return [f for f in (_to_fields(item) for item in data) if f is not None]


def _merge_managed(base: List[Model], managed: List[Model]) -> List[Model]:
    """以 managed 清单为准修正基底：活字段（思考级别/context/输入类型/名称）
    覆盖基底条目，**价格保留基底**（managed 不提供价格）；基底有而
    managed 没有的模型剔除（订阅可见性以 managed 为准）。"""
    base_cost_by_id = {m.id: m.cost for m in base}
    merged: List[Model] = []
    for model in managed:
        base_cost = base_cost_by_id.get(model.id)
        if base_cost is not None:
            model = model.model_copy(
                update={
                    "cost": type(model.cost)(
                        input=base_cost.input,
                        output=base_cost.output,
                        cache_read=base_cost.cache_read,
                        cache_write=base_cost.cache_write,
                    )
                }
            )
        merged.append(model)
    return merged


async def kimi_coding_fetch_models(
    context: RefreshModelsContext,
) -> Optional[List[Model]]:
    """本家 ``fetch_models`` 回调（三件套治理归 ``_default_refresh_models``）。

    公开站基底先行；OAuth 时叠加 managed 鉴权修正层（401/402/403 抛错，
    未给出 200——含传输失败——不占层）。返回 ``None`` = 本次无发布
    （无站且订阅源不可达——缓存保持现状，基线兜底）。
    """
    if is_aborted(context.signal):
        return None

    # 鉴权修正层（可选）：managed 订阅源
    managed_fields = await _managed_fields(context)

    # 基底：公开站（字段包经统一引擎修正+盖戳——协议随站标注）
    fetch = context.catalog_fetch
    outcome = await fetch(context.signal, None) if fetch is not None else None
    if outcome is not None and outcome.status == 200:
        base = finalize_catalog_models(
            "kimi-coding",
            BASE_URL,
            outcome.models_fields,
            knowledge=_knowledge,
        )
        if managed_fields is None:
            return base
        managed = finalize_catalog_models(
            "kimi-coding", BASE_URL, managed_fields, knowledge=_knowledge
        )
        return _merge_managed(base, managed)

    # 基底不可用：managed 降级单独服务（价格落 0，可用性优先）
    if managed_fields is not None:
        context.diagnostics.append(
            NovaDiagnostic(
                severity="warning",
                kind=WarningKind.CAPABILITY,
                message="公开目录站不可用——kimi-coding 由订阅源单独供数（价格为订阅折算价）",
            )
        )
        return finalize_catalog_models(
            "kimi-coding", BASE_URL, managed_fields, knowledge=_knowledge
        )
    if outcome is not None:
        # 不再硬编 UPSTREAM_ERROR：真实非 200 由归一按 status_code 分类，
        # status=0 的合成码场景沿 cause 链取传输层真 kind（分类一次在边界）
        raise CatalogError(
            f"Model catalog request failed for kimi-coding: {outcome.status}",
            status_code=outcome.status or None,
        ) from outcome.cause
    # 无站兜底——None = 本次无发布：内存与落盘缓存保持现状，基线兜底
    # （两类降级分讯：无凭据 / 订阅源不可达）
    credential = context.credential
    if credential is not None and credential.type == "oauth":
        message = "kimi-coding 无公开目录站且订阅源不可达——回退内置基线目录"
    else:
        message = "kimi-coding 未配置公开目录站且无 OAuth 凭据——回退内置基线目录"
    context.diagnostics.append(
        NovaDiagnostic(
            severity="warning",
            kind=WarningKind.CAPABILITY,
            message=message,
        )
    )
    return None


__all__ = [
    "kimi_coding_fetch_models",
]
