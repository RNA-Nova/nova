"""Kimi Coding 账户私活（``personalize`` 钩子：managed 订阅源覆盖层）。

钩子经 ``create_provider(personalize=...)`` 装配进 personalized 刷新策略
（``gateway/strategies.py``）——恢复/拉站/分流/持久化/发布全部归策略；
本模块只含账户层三件事：**拉取**（managed ``/models`` 订阅源）、**翻译**
（响应 → 活字段包）、**合并**（名单筛选 + 活字段覆盖）。

账户层零修正：compat/价格/协议戳/``max_tokens`` 整条继承已修正的基底；
订阅源有而目录站未收录的模型 id **跳过+诊断**（目录站是"模型是什么"
的唯一权威——pi openrouter 同款语义：不认的当作没有）。

协议随站：pi.dev 标 kimi-coding 为 openai-completions（Chat Completions，
与官方 Kimi Code 客户端同协议）。
"""

import os
from typing import Any, Dict, List, Optional, TypedDict

from nova_protocol import Model, NovaDiagnostic, NovaErrorKind, WarningKind

from ...gateway.catalog import CatalogError
from ...gateway.diagnostics import DiagnosticsCollector
from ...gateway.provider import RefreshModelsContext
from ...utils.http import request_with_abort

BASE_URL = "https://api.kimi.com/coding/v1"
"""本家 API endpoint 协议基址（对齐 pi.dev 标注——Chat Completions）。"""

_MANAGED_BASE_URL_DEFAULT = BASE_URL
"""managed ``/models`` 订阅源的基址（与协议基址同根——端点为 {base}/models）。"""

# managed /models 的 think_efforts.valid_efforts 到思考级别表的键序
_KNOWN_LEVELS = ("minimal", "low", "medium", "high", "xhigh", "max")


class ManagedOverlay(TypedDict):
    """managed 活字段包（账户态覆盖层——纯翻译产物，不含修正知识）。"""

    id: str
    name: str
    reasoning: bool
    thinking_level_map: Dict[str, Optional[str]]
    input_types: List[str]
    context_window: int


def _managed_base_url() -> str:
    """managed 端点 base URL（``KIMI_CODE_BASE_URL`` 可覆盖，对齐 kimi-code）。"""
    return (os.environ.get("KIMI_CODE_BASE_URL") or _MANAGED_BASE_URL_DEFAULT).rstrip(
        "/"
    )


def _thinking_level_map(valid_efforts: Optional[List[Any]]) -> Dict[str, Optional[str]]:
    """``think_efforts.valid_efforts`` → 思考级别表（缺席级别 = 不支持）。"""
    valid = {e for e in (valid_efforts or []) if isinstance(e, str)}
    return {
        "off": None,
        **{level: level if level in valid else None for level in _KNOWN_LEVELS},
    }


def _to_overlay(item: Any) -> Optional[ManagedOverlay]:
    """managed /models 单条目 → 活字段包（畸形条目跳过，不拖垮整批）。"""
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
        "context_window": context_length,
    }


async def _managed_overlays(
    context: RefreshModelsContext,
) -> Optional[List[ManagedOverlay]]:
    """managed /models 订阅源 → 活字段包列表（鉴权修正层的活数据）。

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
    return [o for o in (_to_overlay(item) for item in data) if o is not None]


def _merge_managed(
    base: List[Model],
    overlays: List[ManagedOverlay],
    diagnostics: DiagnosticsCollector,
) -> List[Model]:
    """名单以 managed 为准（基底有而 managed 无的剔除）；基底已有的 id
    只覆盖活字段（compat/价格/协议戳/``max_tokens`` 整条继承基底，零修正）；
    基底未收录的 id 跳过+诊断（站不认的当作没有——pi openrouter 同款）。"""
    base_by_id = {m.id: m for m in base}
    merged: List[Model] = []
    skipped: List[str] = []
    for overlay in overlays:
        entry = base_by_id.get(overlay["id"])
        if entry is None:
            skipped.append(overlay["id"])
            continue
        merged.append(
            entry.model_copy(
                update={
                    "name": overlay["name"],
                    "reasoning": overlay["reasoning"],
                    "thinking_level_map": overlay["thinking_level_map"],
                    "input_types": overlay["input_types"],
                    "context_window": overlay["context_window"],
                }
            )
        )
    if skipped:
        diagnostics.append(
            NovaDiagnostic(
                severity="warning",
                kind=WarningKind.CAPABILITY,
                message=(
                    f"kimi-coding 订阅源返回了目录站未收录的模型"
                    f"（{', '.join(skipped)}）——已跳过，站数据更新后自动可见"
                ),
            )
        )
    return merged


async def kimi_personalize(
    base: List[Model],
    context: RefreshModelsContext,
) -> List[Model]:
    """kimi 的 ``personalize`` 钩子：managed 订阅源覆盖层（名单+活字段）。

    无凭据 / 订阅源未给出 200（含传输失败）→ 基底原样（不占层）；
    401/402/403 → 抛错（重登录语义，refresh 记入 errors）。
    """
    overlays = await _managed_overlays(context)
    if overlays is None:
        return base
    return _merge_managed(base, overlays, context.diagnostics)


__all__ = [
    "BASE_URL",
    "kimi_personalize",
]
