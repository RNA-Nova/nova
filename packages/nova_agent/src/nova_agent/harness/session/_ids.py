"""uuidv7 生成（RFC 9562）——pi 从 pi-ai 引入，nova 内联实现。

时间有序是会话 id 的硬要求：JSONL 落盘后按 id 排序即时间序，且同毫秒内随机位
保证不碰撞（``time.time()*1000`` 之类的时间戳 id 会碰撞）。
"""

from __future__ import annotations

import secrets
import time
import uuid

__all__ = ["uuidv7"]


def uuidv7() -> str:
    """生成 RFC 9562 uuidv7：48 位 Unix 毫秒 + 版本 7 + 12 位随机 + 变体 + 62 位随机。"""
    unix_ts_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (unix_ts_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return str(uuid.UUID(int=value))
