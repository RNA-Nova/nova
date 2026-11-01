"""请求超时常量与 SSE idle 看门狗（两协议共享）。

超时治理的分工（横切·并发面，见 ``docs/crosscutting/concurrency.md``）：

- **connect/pool 相**：工程上限 ``CONNECT_TIMEOUT_S``——机制底线，策略层
  关不掉（harness "disabled" 曾把单旋钮整体翻成约 24.8 天，连 connect
  都能挂死回合）；
- **read/write 相**：用户旋钮（``StreamOptions.timeout``）——连接建立后
  的流耐心；
- **SSE 事件级 idle**：:func:`idle_guarded` 逐事件看门狗——连接活着但
  服务端半死（长沉默）时处死当前流，死因 :class:`RequestTimeoutError`
  （kind=REQUEST_TIMEOUT；归一上 kind 后回合层按可重试裁决——流不可
  续传，重来整请求是 codex 断流重连在我们语义下的等价物）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Optional, TypeVar

import httpx
import httpx2

from ...utils.errors import RequestTimeoutError

T = TypeVar("T")

CONNECT_TIMEOUT_S = 10.0
"""connect/pool 相的工程上限（秒）——机制底线，策略层不可关。"""

DEFAULT_STREAM_TIMEOUT_S = 300.0
"""旋钮缺省的流 idle 耐心（秒）——对齐 harness 五档默认；要宽松，防误杀
长思考模型。"""

__all__ = [
    "CONNECT_TIMEOUT_S",
    "DEFAULT_STREAM_TIMEOUT_S",
    "build_request_timeout",
    "build_request_timeout_httpx2",
    "idle_guarded",
    "idle_timeout_of",
]


def _read_phase(knob_s: Optional[float]) -> Optional[float]:
    """旋钮 → read 相秒数：``None``=引擎默认；``0``=显式关闭（None）。"""
    if knob_s is None:
        return DEFAULT_STREAM_TIMEOUT_S
    if knob_s == 0:
        return None
    return knob_s


def build_request_timeout(knob_s: Optional[float]) -> httpx.Timeout:
    """openai SDK 的请求超时：旋钮管 read/write，connect/pool 恒工程上限。

    ``knob_s`` 语义即 ``StreamOptions.timeout``（见其字段注释）。
    """
    read = _read_phase(knob_s)
    return httpx.Timeout(
        connect=CONNECT_TIMEOUT_S, pool=CONNECT_TIMEOUT_S, read=read, write=read
    )


def build_request_timeout_httpx2(knob_s: Optional[float]) -> httpx2.Timeout:
    """anthropic SDK（httpx2 底座）的同构构造。"""
    read = _read_phase(knob_s)
    return httpx2.Timeout(
        connect=CONNECT_TIMEOUT_S, pool=CONNECT_TIMEOUT_S, read=read, write=read
    )


def idle_timeout_of(knob_s: Optional[float]) -> Optional[float]:
    """看门狗的 idle 耐心（秒）——与 read 相同源；``None`` 表示关闭看门狗。"""
    return _read_phase(knob_s)


async def idle_guarded(
    stream: AsyncIterator[T], idle_s: Optional[float]
) -> AsyncIterator[T]:
    """逐事件 idle 看门狗：每次取下一事件套 ``idle_s`` 计时，超时处死当前流。

    ``idle_s=None``（用户显式关闭）时不加任何计时直接透传。处死抛
    :class:`RequestTimeoutError`；流的关闭归调用方收尾路径（两 ``_stream.py``
    的 finally），本包装只管判死。
    """
    if idle_s is None:
        async for event in stream:
            yield event
        return
    iterator = stream.__aiter__()
    while True:
        try:
            async with asyncio.timeout(idle_s):
                event = await iterator.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError as error:
            raise RequestTimeoutError(
                f"Stream idle for {idle_s:g}s without events"
            ) from error
        yield event
