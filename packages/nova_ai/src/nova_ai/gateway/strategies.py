"""刷新策略工厂（刷新治理的唯一实现——两种形态；对齐 pi ``remote-catalog-provider.ts``）。

**恢复 → 拉站 → 分流 → finalize → persist+publish** 的流水线只有这一份，
``create_provider`` 按 provider 形态二选一装配：

- ``overlay_refresh_policy``：纯站形态（公共事实目录）。六件套：恢复
  （isNewer 守卫）→ 4h TTL → ETag 条件请求（有缓存体才带 validator）→
  304/404·501/瞬时分流 → 原料 materialize（可选）→ finalize →
  persist+publish；读取 merge（基线 ∪ 动态）。
- ``personalized_refresh_policy``：站 + 账户钩子形态（目录随凭据变）。
  跳 TTL/ETag（凭据易变每次真拉）；finalize 出基底后调 ``personalize``
  钩子（账户层：kimi 订阅覆盖 / 火山 ark 筛选），持久化钩子返回的成品；
  读取 replace（已发布动态目录整取代，含合法空集——``create_provider`` 置位
  ``provider._dynamic_replaces_baseline``）。

pi 对位：``withRemoteCatalog`` / ``withPersonalizedCatalog``（openrouter）。
降级模型统一：瞬时失败保留缓存并抛错（CatalogError 走归一分类）；
无站则本次无发布——stored 成品继续服役，静态基线兜底。
"""

from __future__ import annotations

import time
from typing import Callable, List, Mapping, Optional, cast

from nova_protocol import (
    AnyModel,
    MaterializedCatalog,
    ModelFields,
    ModelsStoreEntry,
    NovaDiagnostic,
    WarningKind,
    is_aborted,
)

from ..model_catalog import builtin_catalog_generated_at, finalize_catalog_models
from .catalog import CatalogError
from .provider import (
    ModelsPublication,
    PersonalizeFn,
    Provider,
    RefreshModelsContext,
    RefreshModelsFn,
    merge_models,
)

MaterializeCatalogFn = Callable[
    [List[ModelFields], Mapping[str, str]], MaterializedCatalog
]
"""原料站物化回调（字段 + 显式删除集）；由策略闭包持有，不进 Provider 类型。"""

REMOTE_CATALOG_REFRESH_INTERVAL_MS = 4 * 60 * 60 * 1000
"""距上次成功校验在该窗口内则跳过网络（对齐 pi REMOTE_CATALOG_REFRESH_INTERVAL_MS）。"""


def remote_models(
    entry: Optional[ModelsStoreEntry],
    local_generated_at: Optional[int],
) -> List[AnyModel]:
    """取持久化 overlay，带新鲜度竞速守卫（对齐 pi ``remoteModels``）。

    - 无缓存 → 空；
    - 缓存的 ``last_modified`` 缺失或不新于基线生成时间 → 忽略
      （新基线赢过过期的运行时缓存）。
    """
    if entry is None:
        return []
    if local_generated_at is not None and (
        entry.last_modified is None or entry.last_modified <= local_generated_at
    ):
        return []
    return entry.models


def _restore(
    provider: Provider, restored: List[AnyModel], *, has_entry: bool
) -> "Callable[[], None]":
    """恢复段发布动作；无缓存条目只清空，不伪装成已发布的空目录。"""

    def _update() -> None:
        if has_entry:
            provider._set_dynamic_models(restored)
        else:
            provider._dynamic_models = []
            provider._dynamic_published = False

    return _update


def _no_station_diagnostic(provider: Provider, context: RefreshModelsContext) -> None:
    """无站（manager 未发放 catalog_fetch）的统一降级诊断。"""
    context.diagnostics.append(
        NovaDiagnostic(
            severity="warning",
            kind=WarningKind.CAPABILITY,
            message=(
                f"No public catalog service configured for {provider.id}; "
                "using cached/baseline models"
            ),
        )
    )


def overlay_refresh_policy(
    provider: Provider,
    *,
    local_generated_at: Optional[int] = None,
    materialize: Optional[MaterializeCatalogFn] = None,
) -> RefreshModelsFn:
    """纯站刷新策略（六件套治理；公共事实目录——overlay 读取形态）。"""
    if local_generated_at is None:
        local_generated_at = builtin_catalog_generated_at()

    async def _refresh_models(context: RefreshModelsContext) -> None:
        # publish 在 ModelsManager 驱动路径上恒存在；缺席（无世代校验的
        # 直调测试桩）宽容跳过
        publish = context.publish
        if publish is None:
            return
        signal = context.signal
        stored = context.stored
        restored = [
            m
            for m in remote_models(stored, local_generated_at)
            if m.provider == provider.id
        ]
        if not await publish(
            ModelsPublication(
                update=_restore(provider, restored, has_entry=stored is not None)
            )
        ):
            return

        if not context.allow_network or is_aborted(signal):
            return
        fetch = context.catalog_fetch
        if fetch is None:
            _no_station_diagnostic(provider, context)
            return
        # TTL 闸（对齐 pi）：checked_at 与 last_modified 同有才短路——
        # 有缓存体的新鲜快照才跳过；瞬时失败后的无体条目不抑制下次重试
        if (
            not context.force
            and stored is not None
            and stored.checked_at is not None
            and stored.last_modified is not None
            and int(time.time() * 1000) - stored.checked_at
            < REMOTE_CATALOG_REFRESH_INTERVAL_MS
        ):
            return

        # 只有缓存有 body 时才发 validator，304 永远不会让 overlay 变空
        validator = stored.etag if (stored is not None and stored.models) else None
        outcome = await fetch(signal, validator)
        if is_aborted(signal):
            return
        checked_at = int(time.time() * 1000)

        if outcome.status == 304 and stored is not None:
            # 未变：overlay 已在内存，仅推进新鲜度窗口
            await publish(
                ModelsPublication(
                    persist=stored.model_copy(update={"checked_at": checked_at})
                )
            )
            return
        if outcome.status in (404, 501):
            # 该 provider 无远程目录：主动退出，不再硬重试
            base_entry = stored or ModelsStoreEntry(models=[])
            await publish(
                ModelsPublication(
                    persist=base_entry.model_copy(
                        update={
                            "checked_at": checked_at,
                            "last_modified": 0,
                            "etag": None,
                        }
                    ),
                    # 站方撤目录是即时退场语义；不能只清下次启动的缓存，
                    # 却让当前进程继续展示旧 overlay。
                    update=lambda: provider._set_dynamic_models([]),
                )
            )
            return
        if outcome.status != 200:
            # 瞬时失败（对齐 pi）：缓存体与 validator 保持有效，推进
            # checked_at，并经 CatalogError 抛错（归一按 status_code 分类，
            # status=0 沿 cause 链取传输层真 kind）
            base_entry = stored or ModelsStoreEntry(models=[])
            await publish(
                ModelsPublication(
                    persist=base_entry.model_copy(update={"checked_at": checked_at})
                )
            )
            raise CatalogError(
                f"Model catalog request failed for {provider.id}: {outcome.status}",
                status_code=outcome.status or None,
            ) from outcome.cause

        remote_fields = outcome.models_fields
        if materialize is not None:
            materialized = materialize(remote_fields, outcome.source_status)
            if materialized.remove_ids:
                # overlay 的缺席不是删除；没有 replace 基底就不能安全消费
                # 删除集，宁可配置响亮失败，也不能悄悄复活已淘汰模型。
                raise CatalogError(
                    f"Catalog deletions require personalized refresh for {provider.id}"
                )
            remote_fields = materialized.models_fields
        refreshed = cast(
            List[AnyModel],
            finalize_catalog_models(
                provider.id,
                provider.base_url,
                remote_fields,
                source_reasoning=outcome.source_reasoning,
            ),
        )
        if is_aborted(signal):
            return
        entry = ModelsStoreEntry(
            models=refreshed,
            checked_at=checked_at,
            # 缺失/坏头保持未知；remote_models 会将其视为不新于烤入基线。
            last_modified=outcome.last_modified,
            etag=outcome.etag,
        )
        published = remote_models(entry, local_generated_at)
        await publish(
            ModelsPublication(
                persist=entry,
                update=lambda: provider._set_dynamic_models(published),
            )
        )

    return _refresh_models


def personalized_refresh_policy(
    provider: Provider,
    personalize: PersonalizeFn,
    *,
    local_generated_at: Optional[int] = None,
    materialize: Optional[MaterializeCatalogFn] = None,
) -> RefreshModelsFn:
    """账户个性化刷新策略（站 + 钩子；replace 读取形态）。

    跳 TTL/ETag（凭据易变，每次真拉）；404/501 与"远端不新于基线"时
    remote 为空——钩子作用于纯基线（pi personalized 同款）；瞬时失败
    抛错，stored 成品继续服役；持久化的永远是个性化后的成品（离线
    也吃它——pi 同款）。
    """
    if local_generated_at is None:
        local_generated_at = builtin_catalog_generated_at()

    async def _refresh_models(context: RefreshModelsContext) -> None:
        publish = context.publish
        if publish is None:
            return
        signal = context.signal
        stored = context.stored
        # 恢复段：持久化的个性化成品整体恢复（pi 同款——无 isNewer 守卫）
        restored = [
            m
            for m in (stored.models if stored is not None else [])
            if m.provider == provider.id
        ]
        if not await publish(
            ModelsPublication(
                update=_restore(provider, restored, has_entry=stored is not None)
            )
        ):
            return

        if not context.allow_network or is_aborted(signal):
            return
        fetch = context.catalog_fetch
        if fetch is None:
            _no_station_diagnostic(provider, context)
            return
        outcome = await fetch(signal, None)
        if is_aborted(signal):
            return

        if outcome.status not in (200, 404, 501):
            # 瞬时失败（对齐 pi personalized）：抛错，stored 成品继续服役
            raise CatalogError(
                f"Model catalog request failed for {provider.id}: {outcome.status}",
                status_code=outcome.status or None,
            ) from outcome.cause

        # 404/501（站方无此目录）或远端不新于烤入基线 → remote 为空，
        # 钩子作用于纯基线（pi：remote=[] → personalize(builtin)）
        remote_fields = outcome.models_fields if outcome.status == 200 else []
        if (
            outcome.status == 200
            and local_generated_at is not None
            and (
                outcome.last_modified is None
                or outcome.last_modified <= local_generated_at
            )
        ):
            remote_fields = []
        remove_ids = frozenset()
        if materialize is not None:
            materialized = materialize(remote_fields, outcome.source_status)
            remote_fields = materialized.models_fields
            remove_ids = materialized.remove_ids
        base = merge_models(
            list(provider._baseline_models),
            cast(
                List[AnyModel],
                finalize_catalog_models(
                    provider.id,
                    provider.base_url,
                    remote_fields,
                    source_reasoning=outcome.source_reasoning,
                ),
            ),
        )
        if remove_ids:
            # 公开生命周期淘汰作用于 merge 后的完整基底；否则被远程删除的
            # 条目会被静态基线立即补回。账户筛选仍在此之后的 personalize。
            base = [model for model in base if model.id not in remove_ids]
        personalized = await personalize(base, context)
        if is_aborted(signal):
            return
        checked_at = int(time.time() * 1000)
        entry = ModelsStoreEntry(models=list(personalized), checked_at=checked_at)
        await publish(
            ModelsPublication(
                persist=entry,
                update=lambda: provider._set_dynamic_models(list(personalized)),
            )
        )

    return _refresh_models


__all__ = [
    "REMOTE_CATALOG_REFRESH_INTERVAL_MS",
    "overlay_refresh_policy",
    "personalized_refresh_policy",
    "remote_models",
]
