"""会话 bash 引擎（``nova_coding_agent.bash.engine``）测试。

行为``core/bash-executor.ts``。UserTool 类（``user_tools/bash.py``）
测试见 ``tests/user_tools/test_bash.py``。
"""

import asyncio
import os
from pathlib import Path

import pytest
from nova_coding_agent.bash.engine import create_local_bash_operations


@pytest.mark.asyncio
async def test_local_bash_echo(tmp_path: Path):
    operations = create_local_bash_operations()
    result = await operations.execute("echo hello", str(tmp_path), {})
    assert result.exit_code == 0
    assert "hello" in result.output
    assert result.cancelled is False
    assert result.truncated is False


@pytest.mark.asyncio
async def test_local_bash_exit_code(tmp_path: Path):
    operations = create_local_bash_operations()
    result = await operations.execute("exit 42", str(tmp_path), {})
    assert result.exit_code == 42


@pytest.mark.asyncio
async def test_local_bash_cancellation(tmp_path: Path):
    operations = create_local_bash_operations()
    signal = asyncio.Event()

    async def cancel_later():
        await asyncio.sleep(0.05)
        signal.set()

    asyncio.create_task(cancel_later())
    result = await operations.execute("sleep 10", str(tmp_path), {"signal": signal})
    assert result.cancelled is True
    # 取消时退出码为 None
    assert result.exit_code is None


@pytest.mark.asyncio
async def test_local_bash_kill_process_group(tmp_path: Path):
    """abort 时整个进程组被 kill：shell 起的子进程不得成孤儿。"""
    operations = create_local_bash_operations()
    signal = asyncio.Event()
    marker = tmp_path / "child_alive"

    async def cancel_later():
        await asyncio.sleep(0.1)
        signal.set()

    asyncio.create_task(cancel_later())
    # 子 shell 后台写文件（若进程组未被杀干净，0.3s 后文件会出现）
    result = await operations.execute(
        f"(sleep 0.3 && touch {marker}) & wait",
        str(tmp_path),
        {"signal": signal},
    )
    assert result.cancelled is True
    await asyncio.sleep(0.5)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_local_bash_chunk_callback(tmp_path: Path):
    operations = create_local_bash_operations()
    chunks = []

    def on_chunk(text: str):
        chunks.append(text)

    result = await operations.execute(
        "echo hello && echo world", str(tmp_path), {"on_chunk": on_chunk}
    )
    assert result.exit_code == 0
    assert any("hello" in c for c in chunks)


@pytest.mark.asyncio
async def test_local_bash_strips_ansi(tmp_path: Path):
    """输出记录前剥离 ANSI 转义序列。"""
    operations = create_local_bash_operations()
    result = await operations.execute(
        "printf '\\033[31mred\\033[0m\\n'", str(tmp_path), {}
    )
    assert result.exit_code == 0
    assert "red" in result.output
    assert "\x1b" not in result.output


@pytest.mark.asyncio
async def test_local_bash_tail_truncation_and_full_output(tmp_path: Path):
    """超长输出保留尾部，全量输出落临时文件。"""
    operations = create_local_bash_operations()
    # 生成 3000 行（超过 2000 行上限）：前面的行应被截掉
    result = await operations.execute(
        "for i in $(seq 1 3000); do echo line-$i; done",
        str(tmp_path),
        {},
    )
    assert result.exit_code == 0
    assert result.truncated is True
    # 保留尾部
    assert "line-3000" in result.output
    # 头部被截
    assert "line-1\n" not in result.output
    # 全量输出落盘且包含完整内容
    assert result.full_output_path is not None
    full = Path(result.full_output_path).read_text()
    assert "line-1\n" in full
    assert "line-3000" in full
    os.unlink(result.full_output_path)


@pytest.mark.asyncio
async def test_local_bash_spawn_hook(tmp_path: Path):
    """spawn hook 可修改 command/cwd/env。"""
    from nova_harness.core.types.extensions.process import SpawnContext

    def hook(ctx: SpawnContext) -> SpawnContext:
        ctx.env["NOVA_TEST_VAR"] = "hooked"
        return ctx

    operations = create_local_bash_operations(spawn_hook=hook)
    result = await operations.execute("echo $NOVA_TEST_VAR", str(tmp_path), {})
    assert result.exit_code == 0
    assert "hooked" in result.output


@pytest.mark.asyncio
async def test_local_bash_env_extra(tmp_path: Path):
    """env_extra 合并进 spawn 环境（LLM 工具的 env 参数通道）。"""
    operations = create_local_bash_operations()
    result = await operations.execute(
        "echo $NOVA_ENV_EXTRA", str(tmp_path), {"env_extra": {"NOVA_ENV_EXTRA": "x"}}
    )
    assert result.exit_code == 0
    assert "x" in result.output


@pytest.mark.asyncio
async def test_local_bash_external_accumulator(tmp_path: Path):
    """外部传入 accumulator 时引擎不关闭它，调用方可继续快照。"""
    from nova_coding_agent.tools_common.output_accumulator import (
        OutputAccumulator,
        OutputAccumulatorOptions,
    )

    acc = OutputAccumulator(OutputAccumulatorOptions(temp_file_prefix="nova-test"))
    operations = create_local_bash_operations()
    result = await operations.execute("echo owned", str(tmp_path), {"accumulator": acc})
    assert result.exit_code == 0
    # 调用方仍可快照（引擎未关闭）
    snapshot = acc.snapshot()
    assert "owned" in snapshot.content
    acc.close_temp_file()


@pytest.mark.asyncio
async def test_local_bash_untracks_detached_pid(tmp_path: Path):
    """执行完成后 detached pid 解除登记，不残留跟踪表。"""
    from nova_harness.core.utils.child_process import _tracked_detached_child_pids

    operations = create_local_bash_operations()
    await operations.execute("true", str(tmp_path), {})
    assert not _tracked_detached_child_pids


@pytest.mark.asyncio
async def test_local_bash_nonexistent_cwd(tmp_path: Path):
    """cwd 不存在时预检报错，不启动子进程。"""
    operations = create_local_bash_operations()
    result = await operations.execute("echo hi", str(tmp_path / "no-such-dir"), {})
    assert result.exit_code == -1
    assert "Working directory does not exist" in result.output


@pytest.mark.asyncio
async def test_local_bash_background_pipe_no_hang(tmp_path: Path):
    """后台孙进程继承管道：shell 退出后按空闲宽限收尾，不悬挂。"""
    import time as time_module

    operations = create_local_bash_operations()
    start = time_module.monotonic()
    result = await operations.execute("sleep 5 & echo done", str(tmp_path), {})
    elapsed = time_module.monotonic() - start
    assert result.exit_code == 0
    assert "done" in result.output
    # 无宽限机制时会挂到 sleep 5 退出（5s+）
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_local_bash_late_writer_captured(tmp_path: Path):
    """宽限计时器随 chunk 重置：shell 退出后 50ms 内的迟到输出不丢。"""
    operations = create_local_bash_operations()
    result = await operations.execute(
        "(sleep 0.05; echo late) & echo early", str(tmp_path), {}
    )
    assert result.exit_code == 0
    assert "early" in result.output
    assert "late" in result.output


@pytest.mark.asyncio
async def test_local_bash_sigkill_escalation(tmp_path: Path):
    """SIGTERM 被无视时升级 SIGKILL：trap 进程也能被取消，不留僵尸。"""
    import time as time_module

    operations = create_local_bash_operations()
    signal = asyncio.Event()

    async def cancel_later():
        await asyncio.sleep(0.1)
        signal.set()

    asyncio.create_task(cancel_later())
    start = time_module.monotonic()
    # shell 无视 TERM，循环不断重生 sleep——只有 SIGKILL 能终止
    result = await operations.execute(
        "trap '' TERM; while true; do sleep 1; done",
        str(tmp_path),
        {"signal": signal},
    )
    elapsed = time_module.monotonic() - start
    assert result.cancelled is True
    # 0.1s 触发 + 2s TERM 宽限 + ~1s KILL 宽限
    assert elapsed < 4.0
