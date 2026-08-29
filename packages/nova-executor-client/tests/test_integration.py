"""集成测试：连真实 nova-executor"""

import os

import pytest

from nova_executor_client import ExecutorClient

EXECUTOR_URL = os.environ.get("NOVA_EXECUTOR_URL", "ws://127.0.0.1:28080")
EXECUTOR_TOKEN = os.environ.get("NOVA_EXECUTOR_TOKEN", "test-secret-123")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_connect_and_environment_info():
    """测试连接和环境信息"""
    async with ExecutorClient(EXECUTOR_URL, token=EXECUTOR_TOKEN) as client:
        info = await client.environment_info()
        assert info.shell.name


@pytest.mark.asyncio
@pytest.mark.integration
async def test_process_echo():
    """测试进程执行"""
    async with ExecutorClient(EXECUTOR_URL, token=EXECUTOR_TOKEN) as client:
        handle = await client.process.start(
            argv=["echo", "integration-test"],
            cwd="file:///tmp",
        )
        output = await handle.read(wait_ms=2000)
        stdout = b"".join(output.chunks).decode()
        assert "integration-test" in stdout


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fs_write_read():
    """测试文件读写"""
    async with ExecutorClient(EXECUTOR_URL, token=EXECUTOR_TOKEN) as client:
        test_path = "file:///tmp/nova-executor-client-integration.txt"
        test_content = b"integration test content\n"

        await client.fs.write_file(test_path, test_content)
        content = await client.fs.read_file(test_path)
        assert content == test_content

        await client.fs.remove(test_path)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fs_stream_read():
    """测试流式读取"""
    async with ExecutorClient(EXECUTOR_URL, token=EXECUTOR_TOKEN) as client:
        # 创建 1MB 测试文件
        test_path = "file:///tmp/nova-executor-client-stream.bin"
        test_data = b"x" * (1024 * 1024)
        await client.fs.write_file(test_path, test_data)

        chunks = []
        async for chunk in client.fs.read_stream(test_path):
            chunks.append(chunk)

        content = b"".join(chunks)
        assert len(content) == len(test_data)
        assert content == test_data

        await client.fs.remove(test_path)
