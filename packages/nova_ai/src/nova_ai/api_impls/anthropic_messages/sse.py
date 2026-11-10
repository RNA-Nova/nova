"""Anthropic SDK 流接缝：在事件 JSON 解码前修复非法字符串转义。

SDK 的 ``AsyncStream.__stream__`` 经动态分派消费 ``_iter_events()``；本类只
替换该读取口，事件模型、重试、错误帧和资源关闭仍全部走官方 SDK。
"""

import json
from typing import AsyncIterator

from anthropic._streaming import AsyncStream, ServerSentEvent
from anthropic.types import RawMessageStreamEvent

from ...utils.json_parser import parse_json_with_repair

# 只有本协议认识的 JSON 事件才进入修复；ping、[DONE]、proxy.stats 等未知
# 非 JSON 帧原样交给 SDK 过滤，不得把正常完成转成 ERROR。
_ANTHROPIC_JSON_EVENTS = frozenset(
    {
        "message_start",
        "message_delta",
        "message_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "error",
    }
)


class AnthropicEventStream(AsyncStream[RawMessageStreamEvent]):
    """官方事件流的有限 JSON 修复形态（pi parseJsonWithRepair 对位）。"""

    async def _iter_events(self) -> AsyncIterator[ServerSentEvent]:
        async for event in super()._iter_events():
            if not event.data or event.event not in _ANTHROPIC_JSON_EVENTS:
                yield event
                continue
            try:
                json.loads(event.data)
            except json.JSONDecodeError:
                repaired = parse_json_with_repair(event.data)
                event = ServerSentEvent(
                    event=event.event,
                    data=json.dumps(repaired, ensure_ascii=False),
                    id=event.id,
                    retry=event.retry,
                    raw=event.raw,
                )
            yield event


__all__ = ["AnthropicEventStream"]
