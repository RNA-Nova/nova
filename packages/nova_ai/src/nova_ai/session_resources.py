"""会话级资源的集中清理注册处（对齐 pi ``session-resources.ts``）。

各模块把"某会话结束时要释放的资源"（message threads 的续接台账等）
注册为 cleanup；会话宿主（harness 等）在会话终结时调一次
``cleanup_session_resources(session_id)`` 即全部释放。``session_id``
为 ``None`` 时清空全部（进程退场语义）。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Set

SessionResourceCleanup = Callable[[Optional[str]], None]

_CLEANUPS: Set[SessionResourceCleanup] = set()


def register_session_resource_cleanup(
    cleanup: SessionResourceCleanup,
) -> Callable[[], None]:
    """注册清理回调，返回反注册函数（对齐 pi 同名件）。"""
    _CLEANUPS.add(cleanup)

    def unregister() -> None:
        _CLEANUPS.discard(cleanup)

    return unregister


def cleanup_session_resources(session_id: Optional[str] = None) -> None:
    """执行全部已注册清理（单个失败不阻其余，汇聚后统一抛出——对齐 pi）。"""
    errors: List[Exception] = []
    for cleanup in list(_CLEANUPS):
        try:
            cleanup(session_id)
        except Exception as error:  # noqa: BLE001 —— 汇聚语义（pi AggregateError）
            errors.append(error)
    if errors:
        raise ExceptionGroup("Failed to cleanup session resources", errors)


__all__ = [
    "SessionResourceCleanup",
    "cleanup_session_resources",
    "register_session_resource_cleanup",
]
