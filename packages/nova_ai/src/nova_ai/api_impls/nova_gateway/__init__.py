"""Nova Gateway 协议实现（独立官方 SDK → Nova 标准事件流）。"""

from ._stream import stream, stream_simple

__all__ = ["stream", "stream_simple"]
