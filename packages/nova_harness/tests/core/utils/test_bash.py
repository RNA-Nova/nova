"""
本地 Bash 执行测试。
"""

import asyncio
from pathlib import Path

import pytest

from nova_harness.core.utils.bash import (
    BashResult,
    create_local_bash_operations,
    execute_bash,
)


@pytest.mark.asyncio
async def test_local_bash_echo(tmp_path: Path):
    operations = create_local_bash_operations()
    result = await execute_bash("echo hello", str(tmp_path), operations)
    assert isinstance(result, BashResult)
    assert result.exit_code == 0
    assert "hello" in result.output
    assert result.cancelled is False


@pytest.mark.asyncio
async def test_local_bash_exit_code(tmp_path: Path):
    operations = create_local_bash_operations()
    result = await execute_bash("exit 42", str(tmp_path), operations)
    assert result.exit_code == 42


@pytest.mark.asyncio
async def test_local_bash_cancellation(tmp_path: Path):
    operations = create_local_bash_operations()
    signal = asyncio.Event()

    async def cancel_later():
        await asyncio.sleep(0.05)
        signal.set()

    asyncio.create_task(cancel_later())
    result = await execute_bash(
        "sleep 10", str(tmp_path), operations, {"signal": signal}
    )
    assert result.cancelled is True


@pytest.mark.asyncio
async def test_local_bash_chunk_callback(tmp_path: Path):
    operations = create_local_bash_operations()
    chunks = []

    def on_chunk(text: str):
        chunks.append(text)

    result = await execute_bash(
        "echo hello && echo world", str(tmp_path), operations, {"on_chunk": on_chunk}
    )
    assert result.exit_code == 0
    assert any("hello" in c for c in chunks)
