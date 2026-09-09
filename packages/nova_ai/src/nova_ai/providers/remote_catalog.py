"""内置 provider 的远程目录物化包装（对齐 TS ``remote-catalog-provider.ts``）。

给静态内置 provider 叠加一个**可持久化的远程目录 overlay**：

- 基线（A 管线种子）永远打底，远程 overlay 按 id 覆盖/新增（``merge_models``）；
- **新鲜度竞速守卫**：overlay 的 ``last_modified`` 早于基线生成时间
  （``local_generated_at``）时整个忽略——升级带来的新种子赢过过期缓存；
- 治理六件套（对齐 pi）：4 小时 TTL、ETag/304 条件请求、304 永不掏空、
  瞬时失败保缓存并抛错、404/501 主动退出、单次尝试超时。

publish 的一切落盘经 ``RefreshModelsContext`` 的世代校验——被 supersede
的刷新连 ``models-store.json`` 都碰不到。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..gateway.provider import ModelsPublication
from ..gateway.store import ModelsStoreEntry
from ..types.model import Model
from .catalog import (
    ModelFields,
    build_provider_models,
    fetch_ark_status,
    fetch_models_dev,
)

# 距上次成功校验在该窗口内则跳过网络（对齐 TS REMOTE_CATALOG_REFRESH_INTERVAL_MS）
REMOTE_CATALOG_REFRESH_INTERVAL_MS = 4 * 60 * 60 * 1000
# 目录拉取的单次尝试超时（对齐 TS REMOTE_CATALOG_ATTEMPT_TIMEOUT_MS）
REMOTE_CATALOG_ATTEMPT_TIMEOUT_S = 4.0

__all__ = ["with_remote_catalog"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def merge_models(baseline: List[Model], dynamic: List[Model]) -> List[Model]:
    """基线 ∪ overlay：同 id overlay 胜，新 id 追加（对齐 TS mergeModels）。"""
    merged = list(baseline)
    for model in dynamic:
        index = next((i for i, m in enumerate(merged) if m.id == model.id), -1)
        if index >= 0:
            merged[index] = model
        else:
            merged.append(model)
    return merged


def remote_models(
    entry: Optional[ModelsStoreEntry],
    local_generated_at: Optional[int],
) -> List[Model]:
    """取持久化 overlay，带新鲜度竞速守卫（对齐 TS remoteModels）。

    - 无缓存 → 空；
    - 缓存的 ``last_modified`` 缺失或早于等于基线生成时间 → 忽略
      （A 管线的新种子赢过过期的运行时缓存）。
    """
    if entry is None:
        return []
    if local_generated_at is not None and (
        entry.last_modified is None or entry.last_modified <= local_generated_at
    ):
        return []
    return entry.models


@dataclass(frozen=True, kw_only=True)
class CatalogOutcome:
    """目录拉取结果的状态化形态（对齐 pi 的 HTTP 响应语义）。

    status：200（新目录）/ 304（未变，走缓存）/ 404 与 501（该 provider
    无远程目录——主动退出）/ 其他非 200（瞬时失败，保缓存并抛错）。
    """

    status: int
    models_fields: List[ModelFields] = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[int] = None


# ---------------------------------------------------------------------------
# 默认数据源：models.dev（内容）+ 方舟生命周期（volcengine，凭据走 auth 链）
# ---------------------------------------------------------------------------

_MODELS_DEV_ROUND_CACHE: Dict[str, Any] = {"at": 0.0, "data": None}
_MODELS_DEV_ROUND_TTL_S = 60.0


def _default_fetch_catalog(provider_id: str):
    """构造该 provider 的目录拉取器（models.dev 内容 + volcengine 方舟过滤）。"""

    async def _fetch(
        signal: Any, validator: Optional[str], credential: Any
    ) -> CatalogOutcome:
        if signal is not None and getattr(signal, "aborted", False):
            return CatalogOutcome(status=0)

        now = time.monotonic()
        cached = (
            _MODELS_DEV_ROUND_CACHE["data"]
            if now - _MODELS_DEV_ROUND_CACHE["at"] < _MODELS_DEV_ROUND_TTL_S
            else None
        )
        # 注：models.dev 为单体目录，无 per-provider 条件请求语义；
        # etag/304 机制保留在 CatalogOutcome 契约里，供 per-provider 源启用。

        api_key = None
        if credential is not None:
            if getattr(credential, "type", None) == "oauth":
                api_key = getattr(credential, "access", None)
            else:
                api_key = getattr(credential, "key", None)

        try:
            payload = cached if cached is not None else fetch_models_dev()
        except Exception:
            return CatalogOutcome(status=0)

        provider_slice = (payload.get(provider_id) or {}).get("models")
        if not provider_slice:
            return CatalogOutcome(status=404)

        ark_status: Optional[Dict[str, str]] = None
        if provider_id == "volcengine":
            ark_status = await asyncio.to_thread(fetch_ark_status, api_key)

        fields = build_provider_models(
            provider_id, provider_slice, strict=False, ark_status=ark_status
        )
        return CatalogOutcome(
            status=200,
            models_fields=list(fields.values()),
            last_modified=_now_ms(),
        )

    return _fetch


# ---------------------------------------------------------------------------
# 包装器
# ---------------------------------------------------------------------------


def with_remote_catalog(
    provider: Any,
    local_generated_at: Optional[int] = None,
    fetch_catalog: Optional[
        Callable[[Any, Optional[str], Any], Awaitable[CatalogOutcome]]
    ] = None,
) -> Any:
    """给静态内置 provider 叠加可持久化的远程目录 overlay（对齐 TS withRemoteCatalog）。

    ``fetch_catalog(signal, validator, credential) -> CatalogOutcome`` 缺省为
    models.dev + 方舟组合源；测试可注入假拉取器。
    """
    if fetch_catalog is None:
        fetch_catalog = _default_fetch_catalog(provider.id)

    class _RemoteCatalogProvider(type(provider)):
        """基线（原 provider）∪ 远程 overlay 的运行时形态。"""

        def __init__(self) -> None:
            super().__init__(
                **{
                    f.name: getattr(provider, f.name)
                    for f in dataclass_fields(provider)
                }
            )
            self._base_provider = provider
            self._dynamic_models: List[Model] = []

        def get_models(self) -> List[Model]:
            return merge_models(self._base_provider.get_models(), self._dynamic_models)

        async def refresh_models(self, context) -> None:
            stored = context.stored
            restored = [
                m
                for m in remote_models(stored, local_generated_at)
                if m.provider == provider.id
            ]
            if not await context.publish(
                ModelsPublication(
                    update=lambda: setattr(self, "_dynamic_models", restored)
                )
            ):
                return

            if not context.allow_network or context.signal.aborted:
                return
            if (
                not context.force
                and stored is not None
                and stored.checked_at is not None
                and _now_ms() - stored.checked_at < REMOTE_CATALOG_REFRESH_INTERVAL_MS
            ):
                return

            # 只有缓存有 body 时才发 validator，304 永远不会让 overlay 变空
            validator = stored.etag if (stored is not None and stored.models) else None
            outcome = await fetch_catalog(context.signal, validator, context.credential)
            if context.signal.aborted:
                return
            checked_at = _now_ms()

            if outcome.status == 304 and stored is not None:
                # 未变：overlay 已在内存，仅推进新鲜度窗口
                await context.publish(
                    ModelsPublication(
                        persist=stored.model_copy(update={"checked_at": checked_at})
                    )
                )
                return
            if outcome.status in (404, 501):
                # 该 provider 无远程目录：主动退出，不再硬重试
                base_entry = stored or ModelsStoreEntry(models=[])
                await context.publish(
                    ModelsPublication(
                        persist=base_entry.model_copy(
                            update={
                                "checked_at": checked_at,
                                "last_modified": 0,
                                "etag": None,
                            }
                        )
                    )
                )
                return
            if outcome.status != 200:
                # 瞬时失败：缓存体与 validator 保持有效，下次 revalidate
                base_entry = stored or ModelsStoreEntry(models=[])
                await context.publish(
                    ModelsPublication(
                        persist=base_entry.model_copy(update={"checked_at": checked_at})
                    )
                )
                raise RuntimeError(
                    f"Model catalog request failed for {provider.id}: {outcome.status}"
                )

            refreshed = [
                Model(**{**fields, "provider": provider.id})
                for fields in outcome.models_fields
            ]
            if context.signal.aborted:
                return
            entry = ModelsStoreEntry(
                models=refreshed,
                checked_at=checked_at,
                last_modified=(
                    outcome.last_modified
                    if outcome.last_modified is not None
                    else checked_at
                ),
                etag=outcome.etag,
            )
            published = remote_models(entry, local_generated_at)
            await context.publish(
                ModelsPublication(
                    persist=entry,
                    update=lambda: setattr(self, "_dynamic_models", published),
                )
            )

    return _RemoteCatalogProvider()
