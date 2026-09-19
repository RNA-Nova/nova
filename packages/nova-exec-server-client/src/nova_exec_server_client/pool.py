"""传输连接池：按"用途/通道"把 JSON-RPC 方法路由到独立连接。

大文件流式传输（数据面）走独立连接，避免阻塞 LLM 工具调用（控制面）：

- 单通道 `{"control": transport}`：全部方法走一条连接（现状行为）
- 双通道 `{"control": t1, "data": t2}`：控制/数据面分离
- 通道集合任意（经 `method_routes` 自定义方法 → 通道映射），不硬编码双连接

池自身实现 Transport 接口（connect/disconnect/send_request/send_notification/
on_notification/is_connected），各管理器无需感知通道存在：未命中路由表的
方法走默认通道；路由目标通道未配置时回退默认通道（单连接场景自然成立）。

注意：服务端句柄状态（写流/读流/进程）随连接存活，故**每个通道一条连接**，
不做通道内多连接轮询（写流 chunk 跨连接会因"未知句柄"失败）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .protocol import (
    FS_READ_STREAM,
    FS_WRITE_STREAM,
    FS_WRITE_STREAM_CHUNK,
    FS_WRITE_STREAM_DONE,
)
from .transport import Transport

#: 控制面通道（默认通道）：生命周期/环境/进程/小文件等低开销方法
CHANNEL_CONTROL = "control"
#: 数据面通道：大流量流式传输方法
CHANNEL_DATA = "data"

#: 默认数据面方法集（按方法名路由；将来新增数据面方法在此扩展）
DATA_CHANNEL_METHODS: frozenset[str] = frozenset(
    {
        FS_READ_STREAM,
        FS_WRITE_STREAM,
        FS_WRITE_STREAM_CHUNK,
        FS_WRITE_STREAM_DONE,
    }
)


class TransportPool:
    """按通道持有传输实例的连接池（自身实现 Transport 接口，对上层透明）"""

    def __init__(
        self,
        channels: Mapping[str, Transport],
        method_routes: Mapping[str, str] | None = None,
        default_channel: str = CHANNEL_CONTROL,
    ):
        if default_channel not in channels:
            raise ValueError(
                f"default channel `{default_channel}` not in channels: {sorted(channels)}"
            )
        self._channels: dict[str, Transport] = dict(channels)
        self._default_channel = default_channel
        # 方法 → 通道路由表；未命中走默认通道
        self._method_routes: dict[str, str] = (
            dict(method_routes)
            if method_routes is not None
            else {method: CHANNEL_DATA for method in DATA_CHANNEL_METHODS}
        )

    def resolve_channel(self, method: str, channel: str | None = None) -> str:
        """解析方法落点通道名：显式 channel 优先，其次方法名路由表，兜底默认
        通道；目标通道未配置时回退默认通道（单连接即全部落一条连接）。

        通知分发层（notifications.NotificationRouter）按此给注册的流打通道
        标签，连接恢复失败时按通道清扫。
        """
        name = channel or self._method_routes.get(method) or self._default_channel
        return name if name in self._channels else self._default_channel

    def _pick(self, method: str, channel: str | None) -> Transport:
        """按 resolve_channel 的通道名取传输实例"""
        return self._channels[self.resolve_channel(method, channel)]

    def iter_transports(self) -> tuple[Transport, ...]:
        """按通道声明顺序返回全部底层传输（诊断/检视用；恢复包装
        （recovery.ManagedTransport）解包为其当前底层实例）"""
        return tuple(
            getattr(t, "current_transport", t) for t in self._channels.values()
        )

    async def connect(self) -> None:
        """连接全部通道；任一失败回滚已连通道，避免半连接状态"""
        connected: list[Transport] = []
        try:
            for transport in self._channels.values():
                await transport.connect()
                connected.append(transport)
        except Exception:
            for transport in connected:
                await transport.disconnect()
            raise

    async def disconnect(self) -> None:
        """断开全部通道（单条失败不跳过其余）"""
        results = await asyncio.gather(
            *(t.disconnect() for t in self._channels.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> Any:
        """发送 JSON-RPC 请求并等待响应（按通道路由）"""
        return await self._pick(method, channel).send_request(method, params)

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        channel: str | None = None,
    ) -> None:
        """发送 JSON-RPC 通知（按通道路由——写流 chunk 与开句柄同连接）"""
        await self._pick(method, channel).send_notification(method, params)

    def on_notification(self, handler: Callable[[dict], Awaitable[None]]) -> None:
        """注册通知处理器：fan-in 到底层全部连接（流式通知从数据面连接回流）"""
        for transport in self._channels.values():
            transport.on_notification(handler)

    @property
    def is_connected(self) -> bool:
        return all(t.is_connected for t in self._channels.values())
