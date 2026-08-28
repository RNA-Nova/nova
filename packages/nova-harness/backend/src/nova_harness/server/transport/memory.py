"""In-memory transport for testing and embedded use."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from nova_harness.server.transport.base import Transport


class MemoryTransport(Transport):
    """双端内存传输，用于测试。

    创建时传入对端 ``MemoryTransport`` 实例，两者通过内部队列互相收发消息。
    """

    def __init__(self, peer: Optional["MemoryTransport"] = None) -> None:
        self._inbox: List[Dict[str, Any]] = []
        self._binary_inbox: List[tuple[bytes, Dict[str, Any]]] = []
        self._peer = peer
        self._closed = False
        if peer is not None:
            peer._peer = self

    @property
    def supports_binary(self) -> bool:
        return True

    async def open(self) -> None:
        return

    async def read(self) -> Dict[str, Any] | None:
        if self._closed:
            return None
        while not self._inbox:
            if self._closed:
                return None
            # 协程让出，便于测试驱动
            import asyncio

            await asyncio.sleep(0)
        return self._inbox.pop(0)

    async def write(self, msg: Dict[str, Any]) -> None:
        if self._peer is None or self._peer._closed:
            return
        self._peer._inbox.append(msg)

    async def send_binary(
        self, data: bytes, metadata: Dict[str, Any] | None = None
    ) -> None:
        if self._peer is None or self._peer._closed:
            return
        self._peer._binary_inbox.append((data, metadata or {}))

    async def receive_binary(self) -> tuple[bytes, Dict[str, Any]] | None:
        if self._closed:
            return None
        while not self._binary_inbox:
            if self._closed:
                return None
            import asyncio

            await asyncio.sleep(0)
        return self._binary_inbox.pop(0)

    async def close(self) -> None:
        self._closed = True

    def inject(self, msg: Dict[str, Any]) -> None:
        """测试辅助：直接向本端 inbox 注入一条消息。"""
        self._inbox.append(msg)
