"""stdio 集成测试：spawn 真实 nova-executor 二进制（--listen stdio）。

二进制定位顺序：环境变量 NOVA_EXECUTOR_BIN → 仓库内 cargo debug 产物
（packages/nova_executor/target/debug/nova-executor）→ PATH。
找不到即整模块 skip（可用 `cargo build -p nova-executor-cli` 构建）。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from nova_executor import ExecutorClient, ProcessError

_REPO_TARGET = (
    Path(__file__).resolve().parents[2]
    / "nova_executor"
    / "target"
    / "debug"
    / "nova-executor"
)


def _find_executor_bin() -> str | None:
    candidate = os.environ.get("NOVA_EXECUTOR_BIN")
    if candidate and Path(candidate).is_file():
        return candidate
    if _REPO_TARGET.is_file():
        return str(_REPO_TARGET)
    return shutil.which("nova-executor")


EXECUTOR_BIN = _find_executor_bin()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(EXECUTOR_BIN is None, reason="nova-executor 二进制不可用"),
]


def make_client(connections: int = 1) -> ExecutorClient:
    return ExecutorClient.from_stdio(program=EXECUTOR_BIN, connections=connections)


@pytest.mark.asyncio
async def test_stdio_environment_info():
    """stdio 连接：握手 + environment/info"""
    async with make_client() as client:
        info = await client.environment_info()
        assert info.shell.name
        assert info.cwd


@pytest.mark.asyncio
async def test_stdio_process_echo():
    """stdio 连接：进程执行与输出读取"""
    async with make_client() as client:
        handle = await client.process.start(
            argv=["echo", "stdio-integration-test"],
            cwd="file:///tmp",
        )
        output = await handle.read(wait_ms=2000)
        stdout = b"".join(output.chunks).decode()
        assert "stdio-integration-test" in stdout


@pytest.mark.asyncio
async def test_stdio_process_stdin_write():
    """stdio 连接：process/write 写 stdin（覆盖 base64 序列化路径）"""
    async with make_client() as client:
        handle = await client.process.start(
            argv=["cat"],
            cwd="file:///tmp",
            pipe_stdin=True,
        )
        await handle.write(b"hello-stdin\n")
        output = await handle.read(wait_ms=2000)
        stdout = b"".join(output.chunks).decode()
        assert "hello-stdin" in stdout
        # cat 未收到 stdin EOF 不会自行退出；terminate 异步生效，
        # 响应瞬间仍 running 时 SDK 按"终止失败"报错——忽略之，
        # 会话随 disconnect 清理进程
        try:
            await handle.terminate()
        except ProcessError:
            pass


@pytest.mark.asyncio
async def test_stdio_write_stream_roundtrip():
    """fs/writeStream 全链路：分片推流写盘 → readFile 校验 → readStream 回读"""
    async with make_client() as client:
        test_path = "file:///tmp/nova-executor-py-wstream.bin"
        # 1.5MB 随机数据，block_size 64KB → 24 块
        test_data = os.urandom(1024 * 1024 + 512 * 1024)

        async def source():
            for offset in range(0, len(test_data), 100_000):
                yield test_data[offset : offset + 100_000]

        try:
            total = await client.fs.write_stream(
                test_path, source(), block_size=64 * 1024
            )
            assert total == len(test_data)

            content = await client.fs.read_file(test_path)
            assert content == test_data

            streamed = b""
            async for chunk in client.fs.read_stream(test_path):
                streamed += chunk
            assert streamed == test_data
        finally:
            await client.fs.remove(test_path)


@pytest.mark.asyncio
async def test_stdio_write_stream_zero_bytes():
    """空流：写出一个 0 字节文件"""
    async with make_client() as client:
        test_path = "file:///tmp/nova-executor-py-wstream-empty.bin"
        try:
            total = await client.fs.write_stream(test_path, [])
            assert total == 0
            assert await client.fs.read_file(test_path) == b""
        finally:
            await client.fs.remove(test_path)


@pytest.mark.asyncio
async def test_stdio_write_stream_out_of_order_surfaces_at_done():
    """服务端 seq 校验：乱序错误在 done 回报且不留半截文件。

    客户端 write_stream 自身保证顺序，这里直接手发协议流量模拟乱序。
    """
    from nova_executor import ProtocolError

    async with make_client() as client:
        test_path = "file:///tmp/nova-executor-py-wstream-oops.bin"
        transport = client.transport  # 单连接：控制面即全部
        await transport.send_request(
            "fs/writeStream", {"handleId": "oops", "path": test_path}
        )
        await transport.send_notification(
            "fs/writeStream/chunk",
            {"handleId": "oops", "seq": 1, "chunk": "YWI=", "eof": True},
        )
        with pytest.raises(ProtocolError, match="expected seq 0, got 1"):
            await transport.send_request("fs/writeStream/done", {"handleId": "oops"})
        assert not Path("/tmp/nova-executor-py-wstream-oops.bin").exists()


@pytest.mark.asyncio
async def test_stdio_dual_channel_concurrent():
    """connections=2：数据面大流传输期间控制面调用不被阻塞"""
    async with make_client(connections=2) as client:
        with tempfile.TemporaryDirectory() as tmpdir:
            big_path = Path(tmpdir) / "big.bin"
            big_data = os.urandom(8 * 1024 * 1024)
            big_path.write_bytes(big_data)
            uri = f"file://{big_path}"

            async def read_big():
                total = 0
                async for chunk in client.fs.read_stream(uri):
                    total += len(chunk)
                return total

            # 数据面读大文件与控制面 environment/info 并发
            results = await asyncio.gather(
                read_big(), client.environment_info(), client.environment_status()
            )
            assert results[0] == len(big_data)
            assert results[1].shell.name

            # 数据面写流同样走通
            out_uri = f"file://{tmpdir}/out.bin"
            total = await client.fs.write_stream(out_uri, [big_data])
            assert total == len(big_data)
            assert (Path(tmpdir) / "out.bin").read_bytes() == big_data
