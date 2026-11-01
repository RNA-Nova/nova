"""远程目录治理皮（六件套；对齐 pi ``remote-catalog-provider.ts``）。

定位：**harness 层的组合时治理**——nova_ai 只提供公开站（
``catalog_impls/``）与机制（manager 路由发放 ``ctx.catalog_fetch``），
本模块是唯一消费它们的六件套治理实现。pi 对位：``withRemoteCatalog``
在 coding-agent 层包装内置 provider，本模块在 ModelRuntime 组合根做同件事。

六件套：4 小时 TTL（``checked_at`` 与 ``last_modified`` 同有才短路——
首次失败无缓存体下次立即重试）、ETag/304 条件请求、304 永不掏空、
瞬时失败保缓存并抛错（推进 ``checked_at``：有缓存体的失败退避 4h）、
404/501 主动退出、单次尝试超时归站层。
"""

from __future__ import annotations

import time
from typing import List, Optional

from nova_ai.gateway.provider import (
    ModelsPublication,
    Provider,
    RefreshModelsContext,
    RefreshModelsFn,
)
from nova_ai.model_catalog import finalize_catalog_models
from nova_protocol import Model, ModelsStoreEntry, is_aborted

# 距上次成功校验在该窗口内则跳过网络（对齐 pi REMOTE_CATALOG_REFRESH_INTERVAL_MS）
REMOTE_CATALOG_REFRESH_INTERVAL_MS = 4 * 60 * 60 * 1000


def remote_models(
    entry: Optional[ModelsStoreEntry],
    local_generated_at: Optional[int],
) -> List[Model]:
    """取持久化 overlay，带新鲜度竞速守卫（对齐 pi remoteModels）。

    - 无缓存 → 空；
    - 缓存的 ``last_modified`` 缺失或早于等于基线生成时间 → 忽略
      （新基线赢过过期的运行时缓存）。
    """
    if entry is None:
        return []
    if local_generated_at is not None and (
        entry.last_modified is None or entry.last_modified <= local_generated_at
    ):
        return []
    return entry.models


def catalog_refresh_policy(
    provider: Provider,
    local_generated_at: Optional[int] = None,
) -> RefreshModelsFn:
    """远程目录六件套治理皮（harness 组合根装配的唯一刷新策略）。

    内容源 = ``context.catalog_fetch``（manager 按 provider 选配的公开站
    绑定发放）；盖戳素材从 provider 对象取（元数据归各家自有——
    models.json 声明式 provider 同样可用）。
    """

    async def _refresh_models(context: RefreshModelsContext) -> None:
        # publish 与 signal 在 ModelsManager 驱动路径上恒存在；缺 publish
        # 的直调（无世代校验的测试桩场景）宽容跳过
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
                update=lambda: setattr(provider, "_dynamic_models", restored)
            )
        ):
            return

        if not context.allow_network or is_aborted(signal):
            return
        fetch = context.catalog_fetch
        if fetch is None:
            # 未选配站 / 容器缺席：恢复照常，网络阶段静默跳过
            return
        # TTL 闸（对齐 pi）：checked_at 与 last_modified 同有才短路——
        # 有缓存体的新鲜快照才跳过；瞬时失败后的无体条目（last_modified 缺失）
        # 不抑制下次真实重试
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
                    )
                )
            )
            return
        if outcome.status != 200:
            # 瞬时失败（对齐 pi）：缓存体与 validator 保持有效，推进
            # checked_at（有缓存体的失败退避 4h；无缓存体时 last_modified
            # 缺失，上方 TTL 闸不短路——下次立即重试），并抛错
            base_entry = stored or ModelsStoreEntry(models=[])
            await publish(
                ModelsPublication(
                    persist=base_entry.model_copy(update={"checked_at": checked_at})
                )
            )
            raise RuntimeError(
                f"Model catalog request failed for {provider.id}: {outcome.status}"
            )

        refreshed = finalize_catalog_models(
            provider.id,
            provider.base_url,
            outcome.models_fields,
            knowledge=provider.catalog_knowledge,
        )
        if is_aborted(signal):
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
        await publish(
            ModelsPublication(
                persist=entry,
                update=lambda: setattr(provider, "_dynamic_models", published),
            )
        )

    return _refresh_models


__all__ = [
    "REMOTE_CATALOG_REFRESH_INTERVAL_MS",
    "catalog_refresh_policy",
    "remote_models",
]
