"""回调式动态 provider 桩（测试车辆）。

生产侧的 ``fetch_models`` 三件套装配已删除（刷新治理归
``nova_ai.gateway.strategies`` 两策略工厂）。本 helper 仅用于给
manager 刷新机器的测试制造动态 provider——恢复段发布 stored，
网络段调回调、非 None 时 persist+publish（最小纪律，不是三件套的复刻）。
"""

import time
from typing import Awaitable, Callable, List, Optional

from nova_protocol import Model, ModelsStoreEntry, is_aborted

from nova_ai.gateway.provider import (
    ModelsPublication,
    Provider,
    RefreshModelsContext,
    RefreshModelsFn,
)


def callback_refresh(
    provider: Provider,
    fetch: Callable[[RefreshModelsContext], Awaitable[Optional[List[Model]]]],
) -> RefreshModelsFn:
    """给 provider 造一个回调驱动的 ``refresh_models`` 测试桩。"""

    async def _refresh(context: RefreshModelsContext) -> None:
        publish = context.publish
        if context.stored is not None and publish is not None:
            restored = [m for m in context.stored.models if m.provider == provider.id]
            await publish(
                ModelsPublication(
                    update=lambda: setattr(provider, "_dynamic_models", restored)
                )
            )
        if not context.allow_network or is_aborted(context.signal):
            return
        refreshed = await fetch(context)
        if refreshed is None or publish is None:
            return
        await publish(
            ModelsPublication(
                persist=ModelsStoreEntry(
                    models=list(refreshed), checked_at=int(time.time() * 1000)
                ),
                update=lambda: setattr(provider, "_dynamic_models", list(refreshed)),
            )
        )

    return _refresh
