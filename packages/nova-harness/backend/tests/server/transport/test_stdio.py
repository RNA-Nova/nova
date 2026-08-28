"""StdioTransport 真实管道测试（子进程端到端）。

覆盖两个历史隐患：
- 读侧：asyncio 默认 64KB 行限会拒收大帧（带图 prompt/大会话历史）；
- 写侧：同步 sys.stdout.flush() 在大帧（>64KB 管道缓冲）时阻塞事件循环。
"""

import asyncio
import json
import sys

import pytest

# 子进程：StdioTransport 回声服务（读一帧 → 原样写回 → 收到 params.done 或 EOF 关闭）
_ECHO_SCRIPT = """
import asyncio
from nova_harness.server.transport.stdio import StdioTransport

async def main():
    t = StdioTransport()
    await t.open()
    try:
        while True:
            msg = await t.read()
            if msg is None:
                break
            await t.write(msg)
            params = msg.get("params") if isinstance(msg, dict) else None
            if isinstance(params, dict) and params.get("done"):
                break
    finally:
        await t.close()

asyncio.run(main())
"""


async def _spawn_echo():
    """起回声子进程（读侧放宽 limit——父进程读回大帧同理需要）。"""
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _ECHO_SCRIPT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        limit=64 * 1024 * 1024,
    )


@pytest.mark.asyncio
async def test_large_frame_roundtrip_over_64kb():
    """>64KB 帧（带图 prompt 量级）往返完整——读侧不破限、写回不截断。"""
    proc = await _spawn_echo()
    assert proc.stdin is not None and proc.stdout is not None

    payload = "x" * (256 * 1024)  # 256KB，四倍于旧的 64KB 默认限
    frame = {"jsonrpc": "2.0", "id": 1, "method": "echo", "params": {"data": payload}}
    proc.stdin.write(json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n")
    proc.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"done": True}}
        ).encode()
        + b"\n"
    )
    await proc.stdin.drain()

    line1 = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
    echoed = json.loads(line1.decode("utf-8"))
    assert echoed["id"] == 1
    assert echoed["params"]["data"] == payload  # 全文完整，无截断无撕裂

    line2 = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
    assert json.loads(line2.decode("utf-8"))["id"] == 2

    await asyncio.wait_for(proc.wait(), timeout=10.0)
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_write_does_not_block_event_loop():
    """写侧大帧 drain 是协作式让出：写入期间事件循环上的其他 task 照跑。"""
    proc = await _spawn_echo()
    assert proc.stdin is not None and proc.stdout is not None

    # 后台打标 task：若事件循环被阻塞，标记将停滞
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.001)

    ticker_task = asyncio.create_task(ticker())

    # 连续写入多帧大载荷（总量数倍于 64KB 管道缓冲）
    big = "y" * (128 * 1024)
    for i in range(8):
        frame = {"jsonrpc": "2.0", "id": i, "method": "echo", "params": {"data": big}}
        proc.stdin.write(json.dumps(frame).encode() + b"\n")
        await proc.stdin.drain()

    # 排空回声（8 帧普通 + done）
    proc.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": 99, "method": "echo", "params": {"done": True}}
        ).encode()
        + b"\n"
    )
    await proc.stdin.drain()
    for _ in range(9):
        await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)

    stop.set()
    await ticker_task
    await asyncio.wait_for(proc.wait(), timeout=10.0)

    # 写入期间 ticker 持续推进（阻塞冻结则 ticks 趋近于 0）
    assert ticks > 10


@pytest.mark.asyncio
async def test_small_frames_still_work():
    """常规小帧往返（回归：异步化不影响正常路径）。"""
    proc = await _spawn_echo()
    assert proc.stdin is not None and proc.stdout is not None

    frame = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "echo",
        "params": {"text": "含换行\n与中文"},
    }
    proc.stdin.write(json.dumps(frame, ensure_ascii=False).encode("utf-8") + b"\n")
    proc.stdin.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "echo", "params": {"done": True}}
        ).encode()
        + b"\n"
    )
    await proc.stdin.drain()

    line = await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
    echoed = json.loads(line.decode("utf-8"))
    assert echoed["params"]["text"] == "含换行\n与中文"

    await asyncio.wait_for(proc.stdout.readline(), timeout=10.0)
    await asyncio.wait_for(proc.wait(), timeout=10.0)
