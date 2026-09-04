"""无单行长度上限的流式行读取。

asyncio 的 ``StreamReader.readline`` 有 64KB 单行上限，超限抛
``ValueError: Separator is found, but chunk is longer than limit``——子进程
JSONL 里一条消息可以远超此限（大 read/grep 结果、长内容的消息帧），
管道读取必须按块自行切行。总量安全由调用方的进程超时兜底，无需
单行长上限（stdio transport 的 64MB 显式 limit 是同陷阱的另一处先例）。
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

_CHUNK_SIZE = 65536


async def read_lines(stream: asyncio.StreamReader) -> AsyncIterator[str]:
    """按块读取并切行（``readline`` 的 64KB 陷阱替代件）。

    语义对齐 readline 循环：按 ``\\n`` 切分、去行尾 ``\\r``、EOF 时
    冲刷非空残余行；空行照常产出（消费方自行跳过）。
    """
    buf = b""
    while True:
        chunk = await stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        buf += chunk
        while True:
            idx = buf.find(b"\n")
            if idx < 0:
                break
            line = buf[:idx]
            buf = buf[idx + 1 :]
            yield line.decode("utf-8", errors="replace").removesuffix("\r")
    if buf:
        tail = buf.decode("utf-8", errors="replace").removesuffix("\r")
        if tail.strip():
            yield tail
