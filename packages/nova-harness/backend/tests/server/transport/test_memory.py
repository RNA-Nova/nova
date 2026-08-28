"""MemoryTransport 测试。"""

import pytest

from nova_harness.server.transport import MemoryTransport


@pytest.mark.asyncio
async def test_memory_transport_roundtrip():
    a = MemoryTransport()
    b = MemoryTransport(a)

    await a.open()
    await b.open()

    await a.write({"hello": "world"})
    msg = await b.read()
    assert msg == {"hello": "world"}

    await b.write({"reply": "ok"})
    msg = await a.read()
    assert msg == {"reply": "ok"}

    await a.close()
    await b.close()


@pytest.mark.asyncio
async def test_memory_transport_binary():
    a = MemoryTransport()
    b = MemoryTransport(a)

    await a.open()
    await b.open()

    await a.send_binary(b"data", {"name": "x"})
    data, meta = await b.receive_binary()
    assert data == b"data"
    assert meta == {"name": "x"}

    await a.close()
    await b.close()
