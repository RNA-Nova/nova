"""断点续传（write_stream_resumable / read_stream_resumable）单元测试：
断线注入（假传输中途断开）验证续传拼接正确、offset 计算正确、对账失败抛错。"""

from __future__ import annotations

import asyncio
import base64

import pytest
from fake_transport import FakeTransport

from nova_exec_server_client import FileSystemError
from nova_exec_server_client.errors import TransportError
from nova_exec_server_client.fs import FileSystemManager


class FakeWriteStreamServer(FakeTransport):
    """按 fs/writeStream 语义记账的假服务端：文件状态自持，可注入中途断线。

    - `break_plan`：每条流（按开门序）在收满 N 块后断开（后续通知抛
      TransportError——断线窗口内推送丢失）；新一轮开门即愈合（模拟
      recovery 基建重连成功）；None = 不断开
    - `shrink_on_reconcile`：第 N 次 getMetadata 时清空文件（模拟服务端
      状态回退，触发对账缩水检测）
    - `fs/getMetadata` 如实回报当前文件大小（续传对账的 ground truth）
    """

    def __init__(
        self,
        *,
        break_plan: list[int | None] | None = None,
        shrink_on_reconcile: int | None = None,
    ):
        super().__init__()
        self.file = bytearray()
        self.break_plan = break_plan or []
        self.shrink_on_reconcile = shrink_on_reconcile
        self.reconciles = 0
        # 每条流的开门 offset（验证续传起点用）
        self.open_offsets: list[int | None] = []
        self.stream_seqs: list[list[int]] = []
        self._landed_in_stream = 0

    async def send_request(self, method, params=None, *, channel=None):
        params = params or {}
        if method == "fs/writeStream":
            offset = params.get("offset")
            self.open_offsets.append(offset)
            self.stream_seqs.append([])
            self._landed_in_stream = 0
            if offset is None:
                self.file = bytearray()
            elif offset > len(self.file):
                # 与服务端一致：越界 offset（隔洞写）拒绝
                raise TransportError("offset beyond file length")
            return {"handleId": f"w-{len(self.open_offsets)}"}
        if method == "fs/writeStream/done":
            return {"handleId": params["handleId"], "totalBytes": len(self.file)}
        if method == "fs/getMetadata":
            self.reconciles += 1
            if (
                self.shrink_on_reconcile is not None
                and self.reconciles == self.shrink_on_reconcile
            ):
                self.file = bytearray()
            return {
                "isDirectory": False,
                "isFile": True,
                "isSymlink": False,
                "size": len(self.file),
                "createdAtMs": 0,
                "modifiedAtMs": 0,
            }
        return await super().send_request(method, params, channel=channel)

    async def send_notification(self, method, params=None, *, channel=None):
        if method != "fs/writeStream/chunk":
            return await super().send_notification(method, params, channel=channel)
        stream_index = len(self.open_offsets) - 1
        break_after = (
            self.break_plan[stream_index]
            if stream_index < len(self.break_plan)
            else None
        )
        if break_after is not None and self._landed_in_stream >= break_after:
            raise TransportError("injected disconnect")
        # 落盘（chunk 已按 seq 严格序到达，定位写即顺序追加）
        self.file.extend(base64.b64decode(params["chunk"]))
        self.stream_seqs[-1].append(params["seq"])
        self._landed_in_stream += 1


@pytest.mark.asyncio
async def test_write_stream_resumable_recovers_and_concatenates():
    """中途断线：对账服务端文件大小后带 offset 续传，拼接完整"""
    # 首轮收满 2 块（hel/lo_）后断开，第二轮干净
    server = FakeWriteStreamServer(break_plan=[2, None])
    fs = FileSystemManager(server)
    data = b"hello world"

    total = await fs.write_stream_resumable("file:///tmp/out.bin", data, block_size=4)

    assert total == len(data)
    assert bytes(server.file) == data
    # 首轮从头（offset 省略）；首轮落地 8 字节，续传轮从断点起
    assert server.open_offsets == [None, 8]
    # 每条流的 seq 各自从 0 起
    assert server.stream_seqs[0] == [0, 1]
    assert server.stream_seqs[1] == [0, 1]


@pytest.mark.asyncio
async def test_write_stream_resumable_done_reconciliation_mismatch_fails():
    """done 的 totalBytes 与推送不符 → 对账失败抛错"""
    server = FakeWriteStreamServer()
    fs = FileSystemManager(server)
    # 服务端谎报 totalBytes（模拟对账失败面）
    original = server.send_request

    async def lying_done(method, params=None, *, channel=None):
        if method == "fs/writeStream/done":
            return {"handleId": params["handleId"], "totalBytes": 999}
        return await original(method, params, channel=channel)

    server.send_request = lying_done  # type: ignore[method-assign]

    with pytest.raises(FileSystemError, match="reconciliation failed"):
        await fs.write_stream_resumable("file:///tmp/out.bin", b"abc", block_size=2)


@pytest.mark.asyncio
async def test_write_stream_resumable_server_shrink_fails():
    """对账时服务端文件比已确认还小（服务端状态回退）→ 对账失败抛错"""
    # 首轮收 1 块（2 字节）断开 → 对账得 confirmed=2；第二轮首块再断 →
    # 第二次对账时文件被清空（缩水 2→0）→ 拒绝
    server = FakeWriteStreamServer(break_plan=[1, 0], shrink_on_reconcile=2)
    fs = FileSystemManager(server)

    with pytest.raises(FileSystemError, match="shrank"):
        await fs.write_stream_resumable("file:///tmp/out.bin", b"aabbcc", block_size=2)


@pytest.mark.asyncio
async def test_write_stream_resumable_rejects_non_replayable_source():
    """一次性生成器在需要续传时无法重放 → 明确报错而非静默错数据"""
    server = FakeWriteStreamServer(break_plan=[0])
    fs = FileSystemManager(server)

    def one_shot():
        yield b"aa"
        yield b"bb"

    with pytest.raises(FileSystemError, match="无法重放"):
        await fs.write_stream_resumable("file:///tmp/out.bin", one_shot(), block_size=2)


class FakeReadStreamServer(FakeTransport):
    """fs/readStream 假服务端：内容自持，首轮按 `drop_chunk` 丢尾（真实断连
    丢的是尾巴不是洞）后收尾——done 报单流语义（本轮推送的全部字节数，
    断尾轮服务端以为推了全部），客户端按实收对账触发 "incomplete" 截断检测。"""

    def __init__(self, content: bytes, *, drop_chunk: int | None = None):
        super().__init__()
        self.content = content
        self.drop_chunk = drop_chunk
        self.first_stream = True
        # 每条流的（handleId, offset）参数
        self.stream_params: list[tuple[str, int]] = []

    async def send_request(self, method, params=None, *, channel=None):
        params = params or {}
        if method == "fs/readStream":
            offset = params.get("offset", 0)
            self.stream_params.append((params["handleId"], offset))
            total = None if self.first_stream else len(self.content)
            return {"handleId": params["handleId"], "totalSize": total}
        return await super().send_request(method, params, channel=channel)

    async def drive(self) -> None:
        """按最近一次 fs/readStream 请求推送块与 done（首轮在 drop_chunk 断尾）。"""
        handle, offset = self.stream_params[-1]
        data = self.content[offset:]
        block = 2
        seq = 0
        for start in range(0, len(data), block):
            if self.first_stream and seq == self.drop_chunk:
                # 断线：此后尾部全丢
                break
            chunk = data[start : start + block]
            await self.handlers[0](
                {
                    "method": "fs/readStream/chunk",
                    "params": {
                        "handleId": handle,
                        "seq": seq,
                        "chunk": base64.b64encode(chunk).decode(),
                        "eof": start + block >= len(data),
                    },
                }
            )
            seq += 1
        # done 报单流语义：本轮服务端推送的全部字节数（断尾轮为全部——
        # 服务端以为推了，客户端按实收对账出 "incomplete"）
        await self.handlers[0](
            {
                "method": "fs/readStream/done",
                "params": {"handleId": handle, "totalBytes": len(data)},
            }
        )
        self.first_stream = False


async def drive_until_done(agen, server) -> bytearray:
    """消费异步生成器：每轮 fs/readStream 请求出现后驱动假服务端推送。"""
    received = bytearray()
    driven = 0
    while True:
        probe = asyncio.create_task(agen.__anext__())
        for _ in range(100):
            await asyncio.sleep(0)
            if len(server.stream_params) > driven:
                driven += 1
                await server.drive()
            if probe.done():
                break
        if probe.done():
            try:
                received.extend(probe.result())
            except StopAsyncIteration:
                break
        else:
            probe.cancel()
            raise AssertionError("流式读卡死（没有新请求也没有新块）")
    return received


@pytest.mark.asyncio
async def test_read_stream_resumable_recovers_and_concatenates():
    """首轮断尾触发截断检测 → 按已收字节 offset 重开续读，拼接完整"""
    content = b"hello world"
    server = FakeReadStreamServer(content, drop_chunk=1)
    fs = FileSystemManager(server)

    received = await drive_until_done(
        fs.read_stream_resumable("file:///tmp/in.bin", block_size=2), server
    )

    assert bytes(received) == content
    # 首轮从头（offset 0）；首轮在 seq 1 处断尾（实收 he 共 2 字节），
    # 续读从断点起
    assert [offset for _, offset in server.stream_params] == [0, 2]


@pytest.mark.asyncio
async def test_read_stream_resumable_server_error_not_resumed():
    """服务端真实读失败（done 携带 error）不重试，原样抛出"""
    server = FakeReadStreamServer(b"ab")
    fs = FileSystemManager(server)

    agen = fs.read_stream_resumable("file:///tmp/in.bin", block_size=2)
    first = asyncio.create_task(agen.__anext__())
    for _ in range(50):
        if server.stream_params:
            break
        await asyncio.sleep(0.01)
    assert server.stream_params
    handle, _offset = server.stream_params[0]
    await server.handlers[0](
        {
            "method": "fs/readStream/done",
            "params": {
                "handleId": handle,
                "totalBytes": 0,
                "error": "Permission denied",
            },
        }
    )
    with pytest.raises(FileSystemError, match="Permission denied"):
        await first
    # 只开了一条流（无续传重试）
    assert len(server.stream_params) == 1
