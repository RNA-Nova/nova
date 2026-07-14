"""bash tool executor 单元测试（对齐 TS 行为）。"""

import asyncio
import os
import tempfile

import pytest


def _load_executor(tool_name: str = "bash"):
    import importlib.util

    tool_dir = os.path.join(os.path.dirname(__file__), "..", "tools", tool_name)
    executor_path = os.path.join(tool_dir, "executor.py")
    spec = importlib.util.spec_from_file_location(
        f"_test_tool_{tool_name}", executor_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ToolExecutor()


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_bash_echo(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "echo hello", "cwd": tmpdir}))

    assert result.details["returncode"] == 0
    assert "hello" in result.content[0].text
    assert result.details.get("truncated") is False


def test_bash_stderr(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "echo err >&2", "cwd": tmpdir}))

    assert result.details["returncode"] == 0
    assert "err" in result.content[0].text


def test_bash_non_zero_exit(tmpdir):
    executor = _load_executor()
    result = _run(executor.execute("id", {"command": "exit 42", "cwd": tmpdir}))

    assert result.details["returncode"] == 42
    assert "❌" in result.content[0].text


def test_bash_timeout(tmpdir):
    executor = _load_executor()
    result = _run(
        executor.execute("id", {"command": "sleep 10", "cwd": tmpdir, "timeout": 0.1})
    )

    assert "超时" in result.content[0].text


def test_bash_cancel(tmpdir):
    from nova_agent import AbortController

    executor = _load_executor()
    controller = AbortController()

    async def run_and_cancel():
        task = asyncio.create_task(
            executor.execute(
                "id", {"command": "sleep 10", "cwd": tmpdir}, signal=controller.signal
            )
        )
        await asyncio.sleep(0.1)
        controller.abort()
        return await task

    result = _run(run_and_cancel())
    assert "取消" in result.content[0].text


def test_bash_truncation_creates_temp_file(tmpdir):
    executor = _load_executor()
    # 生成超过 50KB 的输出
    result = _run(
        executor.execute(
            "id",
            {"command": "python3 -c \"print('A' * 100000)\"", "cwd": tmpdir},
        )
    )

    assert result.details.get("truncated") is True
    assert result.details.get("full_output_path")
    assert os.path.exists(result.details["full_output_path"])


def test_bash_on_update_streaming(tmpdir):
    executor = _load_executor()
    updates = []

    async def on_update(result):
        updates.append(result)

    _run(
        executor.execute(
            "id",
            {"command": "echo line1 && echo line2", "cwd": tmpdir},
            on_update=on_update,
        )
    )

    # 至少有一次更新（开始/中间/结束）
    assert len(updates) >= 1
    # 最终更新包含完整输出
    assert "line1" in updates[-1].content[0].text
    assert "line2" in updates[-1].content[0].text


def test_bash_missing_cwd():
    executor = _load_executor()
    result = _run(
        executor.execute("id", {"command": "echo hi", "cwd": "/nonexistent/path"})
    )

    assert "工作目录不存在" in result.content[0].text


def test_bash_missing_command():
    executor = _load_executor()
    result = _run(executor.execute("id", {}))

    assert "必须提供 command 参数" in result.content[0].text
